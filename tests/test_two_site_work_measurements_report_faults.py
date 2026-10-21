"""Schema2 complete raw-ordinal, clock, custody and hard-budget conformance."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import ast
from contextlib import contextmanager
import copy
from fractions import Fraction
import json
from pathlib import Path

import pytest

from scripts import two_site_accepted_work_evidence as files
from tests.two_site_accepted_work_cost_support import custody, collect, whole
from tests.test_two_site_accepted_work_cost_report_faults import fixed
from tests.test_two_site_work_measurements_report import (
    subject, ReportRepository, cost_record, annotate, blank, raw_pair, declared_state,
    collect_measurements, whole_measurements, golden_measurements, read_counter,
    rational, PRIVATE, METRICS, KINDS, NS, canonical, digest,
)


@pytest.mark.parametrize("fault", ["missing", "null", "version", "bool-version", "extra", "domain",
    "list", "bool-ns", "float-ns", "negative-ns", "overflow-ns", "half-end", "backwards-utc", "backwards-mono"])
def test_observation_schema_is_strict_and_never_manufactures_zero(tmp_path, monkeypatch, fault):
    repo = ReportRepository(tmp_path / "repo")
    spec = annotate(repo.specs[0])
    raw = spec["raw"]
    observation = raw["execution_observation"]
    if fault == "missing":
        del raw["execution_observation"]
    elif fault == "null":
        raw["execution_observation"] = None
    elif fault == "version":
        observation["schema_version"] = 2
    elif fault == "bool-version":
        observation["schema_version"] = True
    elif fault == "extra":
        observation["prompt"] = PRIVATE
    elif fault == "domain":
        observation["clock_domain"] = "different-process"
    elif fault == "list":
        observation["active"] = []
    else:
        key = "end_utc_ns" if fault in ("half-end", "backwards-utc") else "end_monotonic_ns" if fault == "backwards-mono" else "start_utc_ns"
        observation["active"][key] = {
            "bool-ns": True, "float-ns": 1.0, "negative-ns": -1, "overflow-ns": 10 ** 21 + 1,
            "half-end": None, "backwards-utc": 89 * NS, "backwards-mono": 4 * NS}[fault]
    for kind in KINDS:
        declared_state(spec, kind, "UNOBSERVED" if fault == "missing" else "INVALID_OBSERVATION")
    repo.save_metrics()
    value, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, wire)
    active = value["measurements"]["timing"]["active_seconds"]
    assert active["observed_subtotal"] == rational(0) and active["unknown_group_count"] == 1
    assert active["full_total"] is None and value["measurements"]["rates"]["active_seconds_per_accepted_behavior"] is None


@pytest.mark.parametrize("fault", ["partial", "bool", "negative", "too-many", "sum", "failed", "correction", "first-type", "false-pair", "taxonomy"])
def test_provider_measurements_have_complete_typed_coverage_not_count_guesses(tmp_path, monkeypatch, fault):
    repo = ReportRepository(tmp_path / "repo")
    spec = annotate(repo.specs[0])
    provider = spec["raw"]["provider_metrics"]
    if fault == "partial":
        provider["state"] = "PARTIAL"
        # State is an old cost dimension; independently declare its new normalized value.
        spec["normalized"]["provider_state"] = "PARTIAL"
        spec["normalized"]["group_eligible"] = False
        spec["normalized"]["observed_candidate"] = False
        spec["normalized"]["provider_usage"]["full_usage"] = None
        spec["provider_full"] = False
    elif fault == "bool":
        provider["worker_attempts"] = True
    elif fault == "negative":
        provider["failed_attempts"] = -1
    elif fault == "too-many":
        provider["worker_attempts"] = 33
    elif fault == "sum":
        provider["worker_attempts"] = 2
    elif fault == "failed":
        provider["failed_attempts"] = 3
    elif fault == "correction":
        provider["successful_correction_rounds"] = 2
    elif fault == "first-type":
        provider["first_pass"] = 1
    elif fault == "false-pair":
        provider.update(worker_attempts=2, reviewer_attempts=0)
    else:
        provider["rework_taxonomy"]["rework_path"] = PRIVATE
    if fault == "taxonomy":
        spec["measurement"]["provider"]["rework_taxonomy"] = None
    else:
        spec["measurement"]["provider"] = blank()["provider"]
    repo.save_metrics()
    value, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, wire)
    assert value["measurements"]["rates"]["retry_rate"] is None
    assert PRIVATE not in json.dumps(value)


@pytest.mark.parametrize("field", ["active", "first_pass", "bool-vs-int", "presence", "unsupported-version"])
def test_v1_equal_cost_rows_require_all_raw_ordinal_measurements_to_agree(tmp_path, monkeypatch, field):
    repo = ReportRepository(tmp_path / "repo")
    first = annotate(repo.specs[0])
    if field == "presence":
        first["raw"]["provider_metrics"]["first_pass"] = None
    second = copy.deepcopy(first)
    if field == "active":
        second["raw"]["execution_observation"]["active"]["end_monotonic_ns"] += 1
    elif field == "first_pass":
        second["raw"]["provider_metrics"]["first_pass"] = False
    elif field == "bool-vs-int":
        second["raw"]["provider_metrics"]["first_pass"] = 1
    elif field == "presence":
        del second["raw"]["provider_metrics"]["first_pass"]
    else:
        first["raw"]["execution_observation"]["schema_version"] = 2
        second["raw"]["execution_observation"]["schema_version"] = 3
    repo.specs.append(second)
    first["measurement"] = blank()
    first["measurement"]["state"] = "CONFLICT"
    first["measurement"]["states"] = dict.fromkeys(KINDS, "CONFLICT")
    repo.save_metrics()
    value, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, wire, conflict=True)
    assert value["costs"]["duplicate_record_count"] == 1
    assert value["measurements"]["rows"][0]["metrics_ordinals"] == [0, 1]
    assert value["measurements"]["coverage"]["conflict_group_count"] == 1
    assert all(rate is None for rate in value["measurements"]["rates"].values())
    # Cost/progress ABI remains identical despite a schema2 measurement conflict.
    v1, old_wire = collect(repo, monkeypatch)
    whole(repo, v1, old_wire)
    assert v1["accepted_behavior_count"] == 1 and v1["costs"]["distinct_record_count"] == 1


@pytest.mark.parametrize("mode", ["cross-run", "cross-kind", "outside-window"])
def test_overlap_is_not_double_counted_or_hidden_by_window_filter(tmp_path, monkeypatch, mode):
    repo = ReportRepository(tmp_path / "repo")
    first = annotate(repo.specs[0])
    window = None
    if mode == "cross-kind":
        first["raw"]["execution_observation"]["quota_wait"] = raw_pair(95, 105, 10, 10)
        declared_state(first, "quota_wait", "OVERLAPPING_INTERVALS")
        declared_state(first, "active", "OVERLAPPING_INTERVALS")
    else:
        second = annotate(cost_record(repo.views[0], "overlap-failure", state="BLOCKED", finished=105),
                          start=95, end=105, mono=20)
        repo.specs.append(second)
        declared_state(first, "active", "OVERLAPPING_INTERVALS")
        declared_state(second, "active", "OVERLAPPING_INTERVALS")
        if mode == "outside-window":
            # Select only one row by finish, but retain both interval proofs.
            first["raw"]["finished_at"] = 90
            first["normalized"]["dimensions"]["finished_at"] = 90.0
            window = {"start_utc": 100, "end_utc": 110}
    repo.save_metrics()
    value, wire = collect_measurements(repo, monkeypatch, window)
    whole_measurements(repo, value, wire, window, overlaps=True)
    if mode == "outside-window":
        assert value["window"]["selected_ordinals"] == [1]
        rows = value["measurements"]["rows"]
        assert [row["window_state"] for row in rows] == ["EXCLUDED", "SELECTED"]
        assert [row["metrics_ordinals"] for row in rows] == [[0], [1]]
        assert all(row["timing_states"]["active"] == "OVERLAPPING_INTERVALS"
                   and row["timing_seconds"]["active"] is None for row in rows)
    assert value["measurements"]["timing"]["active_seconds"]["full_total"] is None
    assert value["measurements"]["timing"]["active_seconds"]["observed_subtotal"] == rational(0)
    assert "OVERLAPPING_OBSERVATIONS" in value["measurements"]["limitations"]


@pytest.mark.parametrize("kinds", [("active", "quota_wait"), ("active", "planned_stop"),
    ("quota_wait", "planned_stop")])
@pytest.mark.parametrize("mode", ["overlap", "adjacent", "zero"])
def test_same_domain_monotonic_overlap_cannot_hide_behind_disjoint_utc(tmp_path, monkeypatch, kinds, mode):
    repo = ReportRepository(tmp_path / "repo")
    spec = annotate(repo.specs[0])
    raw = spec["raw"]["execution_observation"]
    for kind in KINDS:
        raw[kind] = None
        declared_state(spec, kind, "UNOBSERVED")
    left, right = kinds
    raw[left] = raw_pair(90, 100, 5, 10)
    raw[right] = raw_pair(100, 100 if mode == "zero" else 110,
                          15 if mode == "adjacent" else 10, 0 if mode == "zero" else 10)
    if mode == "overlap":
        declared_state(spec, left, "OVERLAPPING_INTERVALS")
        declared_state(spec, right, "OVERLAPPING_INTERVALS")
    else:
        declared_state(spec, left, "OBSERVED", 10)
        declared_state(spec, right, "OBSERVED", 0 if mode == "zero" else 10)
    repo.save_metrics()
    value, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, wire, overlaps=mode == "overlap")
    timing = value["measurements"]["timing"]
    for kind in kinds:
        expected = None if mode == "overlap" else rational(0 if mode == "zero" and kind == right else 10)
        assert timing[kind + "_seconds"]["full_total"] == expected
    assert ("OVERLAPPING_OBSERVATIONS" in value["measurements"]["limitations"]) == (mode == "overlap")


def test_disjoint_utc_different_runs_never_compare_monotonic_epochs(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    annotate(repo.specs[0])
    repo.specs.append(annotate(cost_record(repo.views[0], "different-clock-domain", state="BLOCKED", finished=110),
        start=100, end=110, mono=10, duration=10))
    repo.save_metrics()
    value, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, wire)
    assert value["measurements"]["timing"]["active_seconds"]["full_total"] == rational(20)
    assert "OVERLAPPING_OBSERVATIONS" not in value["measurements"]["limitations"]


def test_zero_is_observed_only_from_a_complete_zero_pair_and_history_stays_unknown(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    annotate(repo.specs[0], start=100, end=100, mono=5, duration=0)
    repo.save_metrics()
    value, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, wire)
    assert value["measurements"]["rates"]["active_seconds_per_accepted_behavior"] == rational(0)
    legacy = cost_record(repo.views[0], "legacy-failed", state="BLOCKED", finished=80)
    repo.specs.append(legacy)
    repo.save_metrics()
    later, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, later, wire)
    timing = later["measurements"]["timing"]["active_seconds"]
    assert timing["observed_group_count"] == timing["unknown_group_count"] == 1
    assert timing["full_total"] is None and timing["observed_subtotal"] == rational(0)


def test_unlocatable_utc_pair_is_not_nonoverlap_or_calendar_authority(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    spec = annotate(repo.specs[0], start=100, end=100, mono=5, duration=10)
    declared_state(spec, "active", "UNLOCATABLE_INTERVAL")
    repo.save_metrics()
    value, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, wire)
    assert value["measurements"]["timing"]["active_seconds"]["full_total"] is None


def test_report_is_readonly_source_free_and_retains_all_legacy_costs(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    annotate(repo.specs[0])
    failed = cost_record(repo.views[0], "legacy-unknown-failure", state="BLOCKED", codex=None, provider=None, pair=None)
    failed["raw"].update(error=PRIVATE, summary=PRIVATE, command=PRIVATE)
    repo.specs.append(failed)
    repo.save_metrics()
    paths = set(repo.artifacts) | {"docs/refactor/two-server/section-1/00-stage-ledger.md", repo.receipt_path}
    before = {path: ((repo.root / path).read_bytes(), (repo.root / path).stat().st_ino,
                     (repo.root / path).stat().st_mode) for path in paths}
    value, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, wire)
    assert before == {path: ((repo.root / path).read_bytes(), (repo.root / path).stat().st_ino,
                            (repo.root / path).stat().st_mode) for path in paths}
    assert value["costs"]["totals"]["codex"]["unknown_weight"] == rational(1)
    assert value["measurements"]["rates"]["active_seconds_per_accepted_behavior"] is None


@pytest.mark.parametrize("window", [False, [], {}, {"start_utc": True, "end_utc": 2},
    {"start_utc": 0, "end_utc": 0}, {"start_utc": 0, "end_utc": 10 ** 12 + 1}])
def test_v2_window_rejects_before_any_owner_io(monkeypatch, window):
    def forbidden(*args):
        pytest.fail("invalid outer input opened owner")
    monkeypatch.setattr(subject, "_proof_snapshot", forbidden)
    fixed("INVALID_WINDOW", lambda: subject.summarize_accepted_work_measurements("/not-opened", window=window))


@pytest.mark.parametrize("fault,code", [
    ("deadline", "TIME_LIMIT"), ("metadata", "COUNT_LIMIT"), ("bytes", "BYTE_LIMIT"),
    ("custody", "SNAPSHOT_DRIFT"), ("close", "UNSAFE_FILE"),
    ("memory", "COUNT_LIMIT"), ("recursion", "COUNT_LIMIT")])
def test_same_owner_resource_poison_and_final_exit_never_returns_partial(tmp_path, monkeypatch, fault, code):
    repo = ReportRepository(tmp_path / "repo")
    annotate(repo.specs[0])
    if fault == "metadata":
        repo.add_work(2)
        annotate(repo.specs[1], start=100, end=102, mono=20, duration=2)
        repo.declare_shared(entries=[{"cost_row_sha256": digest(canonical(repo.specs[0]["normalized"])),
            "metrics_ordinals": [0], "work_fingerprints": sorted(view.work for view in repo.views)}])
    repo.save_metrics()
    factory, actual_run = subject._proof_snapshot, subject._Report.run
    owners, returned, reached, descriptors = [], [], [], []
    @contextmanager
    def tracked(root):
        with factory(root) as owner:
            owners.append(owner)
            if fault == "bytes":
                for index in range(4):
                    name = "bound-" + str(index) + ".json"
                    repo.put("docs/refactor/two-server/section-1/inventory/" + name, b" " * 1998000)
                    owner._read("receipt", name)
                reached.append(True)
            yield owner
    with monkeypatch.context() as patcher:
        patcher.setattr(subject, "_proof_snapshot", tracked)
        if fault == "deadline":
            method = subject._Report._measurements
            def expire(report, *args):
                result = method(report, *args)
                reached.append(True)
                patcher.setattr(custody.time, "monotonic", lambda: report.owner._deadline)
                return result
            patcher.setattr(subject._Report, "_measurements", expire)
        elif fault == "metadata":
            charge = files._EvidenceFiles._charge_metadata
            def overflow(owner, key, size):
                charge(owner, key, size)
                if key[0] == "shared-membership":
                    reached.append(True)
                    charge(owner, ("same-measurement-owner-bound",), 65537)
            patcher.setattr(files._EvidenceFiles, "_charge_metadata", overflow)
        elif fault in ("custody", "close"):
            def body(report):
                value = actual_run(report)
                descriptors.extend(report.owner._handles)
                returned.append(True)
                return value
            patcher.setattr(subject._Report, "run", body)
            if fault == "custody":
                verify = custody._ProofSnapshot._verify
                def drift(owner):
                    if returned:
                        reached.append(True)
                        raise custody._EvidenceSnapshotError("SNAPSHOT_DRIFT")
                    return verify(owner)
                patcher.setattr(custody._ProofSnapshot, "_verify", drift)
            else:
                close = os.close
                def fail_close(fd):
                    close(fd)
                    if returned and not reached and fd in descriptors:
                        reached.append(True)
                        raise OSError(PRIVATE)
                patcher.setattr(files.os, "close", fail_close)
        else:
            def exhaust(raw):
                reached.append(True)
                raise (MemoryError if fault == "memory" else RecursionError)(PRIVATE)
            patcher.setattr(subject, "_measurement_projection", exhaust)
        error = fixed(code, lambda: subject.summarize_accepted_work_measurements(repo.root))
        assert reached and owners and error.__suppress_context__
        assert all(owner._state == "CLOSED" and owner._files._handles == set() for owner in owners)
        for fd in descriptors:
            with pytest.raises(OSError):
                os.fstat(fd)


def test_v2_exact_whole_output_limit_reserves_final_counter_growth(tmp_path, monkeypatch):
    repo = ReportRepository(tmp_path / "repo")
    annotate(repo.specs[0])
    repo.save_metrics()
    canonical = subject._canonical
    reached = []
    for extra in (0, 1):
        def measure(value):
            if type(value) is dict and value.get("state") == "OBSERVATIONAL_ACCEPTED_WORK_MEASUREMENTS":
                growth = 7 - len(str(value["snapshot"]["bytes_read"]))
                reached.append((extra, growth))
                return " " * (2_000_000 - growth + extra)
            return canonical(value)
        with monkeypatch.context() as patcher:
            patcher.setattr(subject, "_canonical", measure)
            if extra:
                fixed("BYTE_LIMIT", lambda: subject.summarize_accepted_work_measurements(repo.root))
            else:
                value, wire = collect_measurements(repo, monkeypatch)
                whole_measurements(repo, value, wire)
    assert len(reached) == 2 and all(growth > 0 for _, growth in reached)
    value, wire = collect_measurements(repo, monkeypatch)
    whole_measurements(repo, value, wire)


@pytest.mark.parametrize("fault", ["symlink", "hardlink", "nonfinite"])
def test_new_api_preserves_physical_metrics_custody_and_strict_json(tmp_path, fault):
    repo = ReportRepository(tmp_path / "repo")
    leaf = repo.root / METRICS
    if fault == "nonfinite":
        repo.artifact(METRICS, b'[{"execution_observation":{"start_utc_ns":NaN}}]\n')
        code = "INVALID_METRICS_LEDGER"
    else:
        body = leaf.read_bytes()
        other = repo.root / "not-metrics.json"
        other.write_bytes(body)
        if fault == "symlink":
            leaf.unlink()
            leaf.symlink_to(other)
        else:
            os.link(leaf, repo.root / "extra-hardlink.json")
        code = "UNSAFE_FILE"
    fixed(code, lambda: subject.summarize_accepted_work_measurements(repo.root))


def test_new_test_byte_bootstrap_and_api_exports_are_exact():
    root = Path(__file__).parent
    names = ("test_two_site_work_measurements_producer.py", "test_two_site_work_measurements_report.py",
             "test_two_site_work_measurements_report_faults.py")
    for name in names:
        body = (root / name).read_bytes()
        assert 0 < len(body) <= 24000 and body.endswith(b"\n") and not body.endswith(b"\n\n")
        assert all(line.rstrip() == line for line in body.splitlines())
        tree = ast.parse(body)
        fence = [node.lineno for node in tree.body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                 and isinstance(node.value.func, ast.Name) and node.value.func.id == "install_p1_00_network_fence"]
        imports = [node.lineno for node in tree.body if isinstance(node, ast.ImportFrom)
                   and node.module and node.module.startswith("scripts")]
        assert len(fence) == 1 and (not imports or fence[0] < min(imports))
    assert set(subject.__all__) == {"AcceptedWorkCostReportError", "summarize_accepted_work",
                                   "summarize_accepted_work_measurements"}
