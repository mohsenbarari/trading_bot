"""Actual offline Producer persistence; no acceptance or savings authority.

The reused four-key tasks are telemetry-reader fixtures, never valid six-key
Runner/Assignment tasks or live Source/Scope/Actor/Git/selector evidence.
"""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import copy
import hashlib
import itertools
import json
from pathlib import Path

from scripts import two_site_cycle_metrics as metrics
from scripts import two_site_work_cost_report as subject
from scripts.two_site_proposal_runner import bounded_file_text, file_fingerprint, unique_json
from tests import test_two_site_controller_provider_metrics as controller
from tests import test_two_site_failed_cycle_metrics as failed
from tests import test_two_site_work_cost_report as fx
from tests import test_two_site_work_cost_retained_call_frame as retained
from tests import test_two_site_work_cost_witness_helpers as h
from tests import test_two_site_work_lineage_consumer as observer

API = "summarize_work_lineage_ledger"
HEAD = "a" * 40
MODEL = "gpt-6.1-sol"
EFFORT = "xhigh"
OBSERVED = "OBSERVED_CONTROLLER_ATTEMPTS"
SENTINEL = observer.SENTINEL_SECRET


def _frozen():
    """Only accepted literal fixtures; never report output."""
    raw = json.dumps(observer.FROZEN_WORK_PAYLOAD, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    assert hashlib.sha256(raw).hexdigest() == observer.FROZEN_WORK_SHA
    for sem, work in ((observer.FROZEN_PROJECTION_A, observer.FROZEN_WORK_LINEAGE_A),
                      (observer.FROZEN_PROJECTION_B, observer.FROZEN_WORK_LINEAGE_B)):
        h.assert_exact_typed(metrics.semantic_identity(sem), sem)
        h.assert_exact_typed(metrics.work_lineage(work), work)
    assert observer.FROZEN_PROJECTION_A != observer.FROZEN_PROJECTION_B
    assert observer.FROZEN_WORK_LINEAGE_A == observer.FROZEN_WORK_LINEAGE_B


def _privacy(value):
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    assert SENTINEL not in text
    assert "CONFIDENTIAL_TELEMETRY_SENTINEL" not in text


def _files(specs):
    """Exact bounded bytes, inode/link/time provenance, and absence witnesses."""
    result = []
    for root, path, limit in specs:
        value = None
        if os.path.lexists(path):
            value = (bounded_file_text(root, path, limit, "custody").encode("utf-8"),
                     file_fingerprint(path.stat(follow_symlinks=False)))
        result.append((root, path, limit, value))
    return result


def _unchanged_files(snapshots):
    for root, path, limit, expected in snapshots:
        if expected is None:
            assert not os.path.lexists(path)
        else:
            assert bounded_file_text(root, path, limit, "custody").encode("utf-8") == expected[0]
            assert file_fingerprint(path.stat(follow_symlinks=False)) == expected[1]


def _source_specs(root, run_dir, attempts):
    specs = [(root, run_dir / "prompt.txt", 200_000),
             (root, run_dir / "stream.jsonl", metrics.MAX_STREAM)]
    for name in attempts:
        path = root / ".local/two-site-refactor/proposals" / name
        specs.extend((root, path / filename, limit) for filename, limit in
                     (("task.json", 480_000), ("status.json", 32_000), ("stream.jsonl", 2_000_000)))
    return specs


def _observation(run_id, semantic, work, state, seconds, tokens, attempts, observed, first_pass,
                 worker, reviewer=None):
    """Independently prescribed raw projection before invoking the Producer."""
    provider_usage = dict(zip(fx.P_KEYS, tokens)) if observed else dict.fromkeys(fx.P_KEYS)
    result = dict(run_id=run_id, model=MODEL, effort=EFFORT, state=state,
        gemini_worker_run_id=worker,
        head_before=HEAD, head_after=HEAD if state == "BLOCKED" else "b" * 40,
        duration_seconds=seconds, finished_at=None, semantic_identity=copy.deepcopy(semantic),
        work_lineage=copy.deepcopy(work), usage_state="OBSERVED_COMPLETED_TURNS",
        input_tokens=100, cached_input_tokens=80, uncached_input_tokens=20, output_tokens=10,
        reasoning_output_tokens=2, completed_turns=1,
        provider_metrics=dict(state=OBSERVED, semantic_identity=copy.deepcopy(semantic),
            work_lineage=copy.deepcopy(work), usage=provider_usage,
            observed_usage_subtotal=dict(zip(fx.P_KEYS, tokens)), attempts=attempts,
            usage_observed_attempts=observed, first_pass=first_pass,
            worker_attempts=1, reviewer_attempts=attempts - 1,
            failed_attempts=1 if state == "BLOCKED" else 0, successful_correction_rounds=0,
            timing_coverage="MATCHING_PROVIDER_ATTEMPTS_ONLY",
            phase_seconds=dict(provider_reported=seconds, outside_provider_unattributed=0.0)))
    if reviewer is not None:
        result.update(gemini_reviewer_run_id=reviewer, proposal_sha256="f" * 64)
    return result


def _assert_observation(record, expected):
    for key in ("gemini_reviewer_run_id", "proposal_sha256"):
        assert (key in record) is (key in expected)
    actual = {key: record[key] for key in expected if key != "provider_metrics"}
    actual["provider_metrics"] = {key: record["provider_metrics"][key]
                                  for key in expected["provider_metrics"]}
    h.assert_exact_typed(actual, expected)
    _privacy(record)
    # Top and nested projections must be distinct Producer-owned dictionaries.
    assert record["semantic_identity"] is not record["provider_metrics"]["semantic_identity"]
    assert record["work_lineage"] is not record["provider_metrics"]["work_lineage"]
    assert record["semantic_identity"] is not expected["semantic_identity"]
    assert record["work_lineage"] is not expected["work_lineage"]


def _produce(root, state_root, run_dir, meta, expected, specs, protected=()):
    raw_files = _files(specs)
    fixed = [meta, expected, observer.FROZEN_WORK_PAYLOAD, observer.FROZEN_PROJECTION_A,
             observer.FROZEN_PROJECTION_B, observer.FROZEN_WORK_LINEAGE_A,
             observer.FROZEN_WORK_LINEAGE_B, *protected]
    snapshots = [(value, h.capture_custody(value)) for value in fixed]
    try:
        _frozen()
        record = metrics.record_cycle(root, state_root, run_dir, meta, MODEL, EFFORT)
        _assert_observation(record, expected)
        return record
    finally:
        for value, snapshot in snapshots:
            h.assert_custody_unchanged(value, snapshot)
        _unchanged_files(raw_files)


def _read_records(root, state_root, run_dirs, returned, observations):
    per_run = []
    specs = []
    for run_dir, expected in zip(run_dirs, observations):
        path = run_dir / "metrics.json"
        raw = bounded_file_text(root, path, 8000, "per-run metrics")
        record = unique_json(raw)
        _assert_observation(record, expected)
        per_run.append(record)
        specs.append((root, path, 8000))
    ledger_path = state_root / "metrics.json"
    ledger_raw = bounded_file_text(root, ledger_path, metrics.MAX_LEDGER, "metrics ledger")
    ledger = unique_json(ledger_raw)
    h.assert_exact_typed(per_run, returned)
    h.assert_exact_typed(ledger, returned)
    for record, expected in zip(ledger, observations):
        _assert_observation(record, expected)
    _privacy(ledger)
    specs.append((root, ledger_path, metrics.MAX_LEDGER))
    return [returned, per_run, ledger], specs


def _lineage(attempts, revisions, records, candidates, cost):
    # fx.lineage multiplies its argument by 64; this is already a frozen digest.
    return dict(work_fingerprint=observer.FROZEN_WORK_SHA, semantic_attempt_count=attempts,
        source_revision_count=revisions, record_count=records,
        observed_candidate_count=candidates, costs=copy.deepcopy(cost))


def _report(n, cost, attempts, revisions, candidates, *, grouped=None,
            lineage_cost=None, unattributed=0, unattributed_cost=None):
    """All arguments are prescribed literals, never values read from Subject."""
    grouped = n if grouped is None else grouped
    lines = [_lineage(attempts, revisions, grouped, candidates,
                     cost if lineage_cost is None else lineage_cost)] if grouped else []
    return fx.report(n, n, cost, lines, attempts=attempts,
        candidates=int(candidates > 0), unattributed=(unattributed, 0, 0),
        unknown_cost=unattributed_cost)


R6_COST = fx.costs((3, 0, (300, 240, 60, 30), True),
    (3, 0, (50, 20, 70), True, 3, 0, 5, 5), (3, 0, 25.0, True))
R6_A_COST = fx.costs((1, 0, (100, 80, 20, 10), True),
    (1, 0, (20, 8, 28), True, 1, 0, 2, 2), (1, 0, 10.0, True))
R6_REMAINING_COST = fx.costs((2, 0, (200, 160, 40, 20), True),
    (2, 0, (30, 12, 42), True, 2, 0, 3, 3), (2, 0, 15.0, True))
FAILED_COST = fx.costs((1, 0, (100, 80, 20, 10), True),
    (1, 0, (10, 4, 14), True, 1, 0, 1, 1), (1, 0, 5.0, True))
UNKNOWN_COST = fx.costs((1, 0, (100, 80, 20, 10), True),
    (1, 0, (0, 0, 0), False, 1, 0, 1, 0), (1, 0, 5.0, True))
COMBINED_COST = fx.costs((2, 0, (200, 160, 40, 20), True),
    (2, 0, (10, 4, 14), False, 2, 0, 2, 1), (2, 0, 10.0, True))


def _exercise(channels, scenarios, files, detached=True, anchors=()):
    """One retained frame preserves all channels, oracles and every prior return.

    Each permutation receives valid -> independent input fault -> SAME valid.
    The invalid top work version is True; frozen identity preflight stays valid.
    """
    fixed = [*channels, observer.FROZEN_WORK_PAYLOAD, observer.FROZEN_PROJECTION_A,
             observer.FROZEN_PROJECTION_B, observer.FROZEN_WORK_LINEAGE_A,
             observer.FROZEN_WORK_LINEAGE_B, *anchors]
    plan = []
    for scenario_index, (select, gold, fault_gold, faulty_run) in enumerate(scenarios):
        fixed.extend((gold, fault_gold))
        for channel_index, channel in enumerate(channels):
            selected = select(channel)
            for ordered in itertools.permutations(selected):
                good = list(ordered)
                fault = copy.deepcopy(good)
                affected = next(row for row in fault if row["run_id"] == faulty_run)
                affected["work_lineage"]["schema_version"] = True
                fixed.extend((good, fault))
                plan.extend(((good, gold), (fault, fault_gold), (good, gold)))
            if detached is True or (scenario_index, channel_index) in detached:
                copied = copy.deepcopy(selected)
                fixed.append(copied)
                plan.append((copied, gold))
    assert len(plan) <= 64 and len(fixed) <= 64
    frame = None
    try:
        frame = retained.RetainedCallFrame(fixed, _frozen)
        for records, expected in plan:
            try:
                _unchanged_files(files)
                with h.armed_hooks():
                    result = frame.call(subject, API, (records,), expected)
                    assert h.get_total_hook_calls() == 0
                _privacy(result)
            finally:
                frame.assert_all_custody()
                _unchanged_files(files)
    finally:
        if frame is not None:
            frame.assert_all_custody()
        _unchanged_files(files)


def test_actual_source_revisions_saved_ledger_and_failed_only(tmp_path: Path):
    state_root = tmp_path / ".local/two-site-refactor/autopilot"
    state_root.mkdir(parents=True)
    returned, run_dirs, observations, sources, metas = [], [], [], [], []
    cases = (
        ("real-a", "real-a-worker-0", "real-a-reviewer-0", "real-task-a", "1" * 64,
         False, observer.FROZEN_PROJECTION_A, observer.FROZEN_WORK_LINEAGE_A),
        ("real-b", "real-b-worker-0", "real-b-reviewer-0", "real-task-b", "2" * 64,
         False, observer.FROZEN_PROJECTION_B, observer.FROZEN_WORK_LINEAGE_B),
        ("real-f", "real-f-worker-0", None, "real-task-f", "1" * 64,
         True, observer.FROZEN_PROJECTION_A, observer.FROZEN_WORK_LINEAGE_A),
    )
    gold = _report(3, R6_COST, 2, 2, 2)
    fault_gold = _report(3, R6_COST, 2, 2, 1, grouped=2,
        lineage_cost=R6_REMAINING_COST, unattributed=1, unattributed_cost=R6_A_COST)
    failed_gold = _report(1, FAILED_COST, 1, 1, 0)
    failed_fault_gold = _report(1, FAILED_COST, 0, 0, 0, grouped=0,
        unattributed=1, unattributed_cost=FAILED_COST)
    for name, worker, reviewer, task, manifest, blocked, semantic, work in cases:
        run_dir = state_root / "runs" / name
        meta = observer._setup_fixture(tmp_path, state_root, run_dir, worker, reviewer,
            task, HEAD, manifest, run_dir.name, blocked=blocked)
        attempts = 1 if blocked else 2
        expected = _observation(name, semantic, work, "BLOCKED" if blocked else "CONTINUE",
            5.0 if blocked else 10.0, (10, 4, 14) if blocked else (20, 8, 28),
            attempts, attempts, None if blocked else True, worker, reviewer)
        specs = _source_specs(tmp_path, run_dir, [worker] if reviewer is None else [worker, reviewer])
        returned.append(_produce(tmp_path, state_root, run_dir, meta, expected,
            sources + specs, protected=metas + returned + observations))
        metas.append(meta)
        run_dirs.append(run_dir)
        observations.append(expected)
        sources.extend(specs)
    channels, persisted = _read_records(tmp_path, state_root, run_dirs, returned, observations)
    files = _files(sources + persisted)
    # All six three-row permutations plus detached records, across all channels.
    # Include failed-only in the SAME frame so every earlier return stays guarded.
    # 54 permutation calls + one detached-return call + nine failed-only calls = 64.
    try:
        _exercise(channels, [
            (lambda rows: rows, gold, fault_gold, "real-a"),
            (lambda rows: [rows[2]], failed_gold, failed_fault_gold, "real-f")],
            files, detached={(0, 0)}, anchors=metas + observations)
    finally:
        _unchanged_files(files)


def test_raw_null_status_stream_known_and_missing_stream_persisted(tmp_path: Path):
    returned, per_run, ledgers, source_specs, persisted_specs, statuses = [], [], [], [], [], []
    gold_known = _report(1, FAILED_COST, 1, 1, 0)
    gold_unknown = _report(1, UNKNOWN_COST, 1, 1, 0)
    gold_combined = _report(2, COMBINED_COST, 1, 1, 0)
    fault_known = _report(1, FAILED_COST, 0, 0, 0, grouped=0,
        unattributed=1, unattributed_cost=FAILED_COST)
    fault_unknown = _report(1, UNKNOWN_COST, 0, 0, 0, grouped=0,
        unattributed=1, unattributed_cost=UNKNOWN_COST)
    fault_combined = _report(2, COMBINED_COST, 1, 1, 0, grouped=1,
        lineage_cost=UNKNOWN_COST, unattributed=1, unattributed_cost=FAILED_COST)
    identity = controller._canonical_identity(
        source_manifest={"scripts/two_site_provider_metrics.py": "1" * 64})
    identity_snapshot = h.capture_custody(identity)
    metas, observations = [], []
    files = None
    try:
        for name, known in (("wire-known", True), ("wire-unknown", False)):
            root = tmp_path / name
            state_root, run_dir = failed._make_cycle_run(root, run_name=name)
            observer._write_turn_stream(run_dir / "stream.jsonl")
            worker = controller._create_attempt(root, "wire-worker-0",
                controller_run_id=run_dir.name, task_id="wire-task", head=HEAD,
                role="worker", state="BLOCKED", usage=None, duration_seconds=5.0,
                gap="docs/ref#semantic", semantic_identity=identity)
            if known:
                # The sole new wire fixture event; existing task/status IO is reused.
                (worker / "stream.jsonl").write_text(json.dumps({"event": "result",
                    "result": {"usage": {"input_tokens": 10, "output_tokens": 4,
                                          "total_tokens": 14}}}) + "\n", encoding="utf-8")
            assert run_dir.name == name
            assert os.path.lexists(worker / "stream.jsonl") is known
            status_path = worker / "status.json"
            assert unique_json(bounded_file_text(root, status_path, 32_000, "status"))["usage"] is None
            statuses.append((root, status_path))
            meta = dict(state="BLOCKED", head_before=HEAD, head_after=HEAD,
                gemini_worker_run_id="wire-worker-0", duration_seconds=5.0)
            expected = _observation(name, observer.FROZEN_PROJECTION_A,
                observer.FROZEN_WORK_LINEAGE_A, "BLOCKED", 5.0,
                (10, 4, 14) if known else (0, 0, 0), 1, int(known), None, "wire-worker-0")
            specs = _source_specs(root, run_dir, ["wire-worker-0"])
            record = _produce(root, state_root, run_dir, meta, expected, specs,
                protected=metas + returned + observations)
            metas.append(meta)
            observations.append(expected)
            channels, persisted = _read_records(root, state_root, [run_dir], [record], [expected])
            returned.extend(channels[0])
            per_run.extend(channels[1])
            ledgers.extend(channels[2])
            source_specs.extend(specs)
            persisted_specs.extend(persisted)
        assert returned[0]["run_id"] == "wire-known"
        assert returned[1]["run_id"] == "wire-unknown"
        files = _files(source_specs + persisted_specs)
        _exercise([returned, per_run, ledgers], [
            (lambda rows: [rows[0]], gold_known, fault_known, "wire-known"),
            (lambda rows: [rows[1]], gold_unknown, fault_unknown, "wire-unknown"),
            (lambda rows: rows, gold_combined, fault_combined, "wire-known")], files,
            anchors=metas + observations + [identity])
    finally:
        h.assert_custody_unchanged(identity, identity_snapshot)
        if files is not None:
            _unchanged_files(files)
        for root, path in statuses:
            assert unique_json(bounded_file_text(root, path, 32_000, "status"))["usage"] is None


def test_candidate_byte_and_format_budget():
    raw = Path(__file__).read_bytes()
    assert 0 < len(raw) <= 24_000
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert all(line == line.rstrip(b" \t\r") for line in raw.splitlines())
