"""Prospective AP observations; fixed clocks, real persistence, no Provider."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import copy
import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import two_site_codex_autopilot as ap
from scripts import two_site_proposal_runner as runner
from scripts import two_site_cycle_metrics as metrics
from tests.test_two_site_cycle_efficiency import canonical_repo, success

NS = 1_000_000_000
PRIVATE = "private-767-never-in-metrics"
ORIGINAL_CAPTURE = runner.capture_bounded_process


def pair(start=90, end=100, mono=5, duration=10):
    return {"start_utc_ns": start * NS, "start_monotonic_ns": mono * NS,
            "end_utc_ns": end * NS, "end_monotonic_ns": (mono + duration) * NS}


def observation(run, active=None):
    return {"schema_version": 1, "clock_domain": run, "active": pair() if active is None else active,
            "quota_wait": None, "planned_stop": None}


def workspace(tmp_path):
    root = tmp_path / "repo"
    state = root / ".local/two-site-refactor/autopilot"
    (state / "runs").mkdir(parents=True, mode=0o700)
    state.chmod(0o700)
    codex = root / "codex"
    codex.write_text("offline fixture")
    return root, state, codex


def install_cycle(monkeypatch, root, events, *, failure=None, preparation=None):
    receipt = success()
    current = [canonical_repo("b" * 40)]
    def validate(*args, **kwargs):
        events.append("validate")
        return current[0]
    def prompt(*args, **kwargs):
        events.append("prepare")
        if preparation == "prompt":
            raise RuntimeError(PRIVATE)
        return PRIVATE
    def identity(*args):
        events.append("readiness")
        if preparation == "readiness":
            raise RuntimeError(PRIVATE)
        return {"sha256": "pin", "runtime": {}}
    def capture(process, _input, output, stderr, *args, **kwargs):
        events.append("capture")
        output.write_text(json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 5, "cached_input_tokens": 2, "output_tokens": 3}}) + "\n")
        if failure is not None:
            raise failure
        (output.parent / "result.json").write_text(json.dumps({k: receipt[k] for k in ap.RESULT_KEYS}))
    def transition(*args):
        events.append("transition")
        current[0] = canonical_repo()
        return receipt
    process = MagicMock()
    process.returncode, process.pid = 0, 123
    process.__enter__.return_value = process
    monkeypatch.setattr(ap, "validate_repository", validate)
    monkeypatch.setattr(ap, "prompt_for_cycle", prompt)
    monkeypatch.setattr(ap, "codex_identity", identity)
    monkeypatch.setattr(ap, "codex_runtime_identity", lambda *a: {})
    monkeypatch.setattr(ap, "file_sha256", lambda *a: "pin")
    monkeypatch.setattr(ap, "capture_bounded_process", capture)
    monkeypatch.setattr(ap, "validate_transition", transition)
    monkeypatch.setattr(ap.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(ap, "_heartbeat_running_cycle", lambda *a, **k: events.append("heartbeat"))
    provider_calls = []
    def provider(*args, **kwargs):
        provider_calls.append(True)
        return {"state": "UNAVAILABLE", "first_pass": None}
    monkeypatch.setattr(metrics, "_call_provider_metrics", provider)
    utc, mono = iter((90 * NS, 100 * NS)), iter((5 * NS, 15 * NS))
    def utc_clock():
        events.append("utc")
        return next(utc)
    def mono_clock():
        events.append("monotonic")
        return next(mono)
    monkeypatch.setattr(metrics.time, "time_ns", utc_clock)
    monkeypatch.setattr(metrics.time, "monotonic_ns", mono_clock)
    return receipt, provider_calls


@pytest.mark.parametrize("case", ["success", "failure", "interrupt", "prompt", "readiness"])
def test_actual_run_cycle_starts_before_preparation_and_closes_after_cleanup(tmp_path, monkeypatch, case):
    root, state, codex = workspace(tmp_path)
    events = []
    original = RuntimeError(PRIVATE) if case == "failure" else KeyboardInterrupt(PRIVATE) if case == "interrupt" else None
    expected, provider_calls = install_cycle(monkeypatch, root, events, failure=original,
        preparation=case if case in ("prompt", "readiness") else None)
    if case == "success":
        run, result = ap.run_cycle(root, codex, state, 1, 300)
        assert result is expected and (state / ap.ACCEPTED_CHECKPOINT_FILENAME).is_file()
    else:
        with pytest.raises(KeyboardInterrupt if case == "interrupt" else RuntimeError) as error:
            ap.run_cycle(root, codex, state, 1, 300)
        if original is not None:
            assert error.value is original
        assert not (state / ap.ACCEPTED_CHECKPOINT_FILENAME).exists()
        run = next((state / "runs").iterdir())
    ledger = json.loads((state / "metrics.json").read_text())
    record = json.loads((run / "metrics.json").read_text())
    status = json.loads((run / "status.json").read_text())
    assert ledger == [record]
    assert record["execution_observation"] == observation(run.name)
    assert status["execution_observation"] == record["execution_observation"]
    assert events[:3] == ["utc", "monotonic", "validate"]
    assert events[-2:] == ["utc", "monotonic"]
    assert events.count("capture") == (1 if case in ("success", "failure", "interrupt") else 0)
    if case in ("success", "failure", "interrupt"):
        assert events.index("heartbeat") < len(events) - 2
    assert record["state"] == ("CONTINUE" if case == "success" else "FAILED")
    assert record["prompt_sha256"] is None if case == "prompt" else record["prompt_sha256"] is not None
    if case in ("prompt", "readiness"):
        assert record["duration_seconds"] is None
    assert provider_calls == [True] and ap._ACTIVE_PROCESS is None
    assert PRIVATE not in json.dumps(record)


def test_failure_before_safe_identity_creates_no_fake_record(tmp_path, monkeypatch):
    root, state, codex = workspace(tmp_path)
    original = RuntimeError(PRIVATE)
    monkeypatch.setattr(ap, "validate_repository", lambda *a, **k: (_ for _ in ()).throw(original))
    with pytest.raises(RuntimeError) as error:
        ap.run_cycle(root, codex, state, 1, 300)
    assert error.value is original
    assert list((state / "runs").iterdir()) == []
    assert not (state / "metrics.json").exists()


@pytest.mark.parametrize("fault", ["recorder", "status", "clock"])
def test_observation_faults_never_replace_success_or_primary_exception(tmp_path, monkeypatch, fault):
    for failed in (False, True):
        root, state, codex = workspace(tmp_path / str(failed))
        original = RuntimeError(PRIVATE) if failed else None
        events = []
        install_cycle(monkeypatch, root, events, failure=original)
        if fault == "recorder":
            monkeypatch.setattr(metrics, "record_cycle", lambda *a, **k: (_ for _ in ()).throw(OSError(PRIVATE)))
        elif fault == "status":
            atomic = metrics.atomic_json
            def failing(root, path, value, limit, _atomic=atomic):
                if path.name == "status.json":
                    raise OSError(PRIVATE)
                return _atomic(root, path, value, limit)
            monkeypatch.setattr(metrics, "atomic_json", failing)
        else:
            monkeypatch.setattr(metrics.time, "time_ns", lambda: (_ for _ in ()).throw(ValueError(PRIVATE)))
        if failed:
            with pytest.raises(RuntimeError) as error:
                ap.run_cycle(root, codex, state, 1, 300)
            assert error.value is original
        else:
            _, result = ap.run_cycle(root, codex, state, 1, 300)
            assert result["state"] == "CONTINUE"


@pytest.mark.parametrize("fault", ["version", "bool-version", "extra", "domain", "list", "bool",
    "negative", "overflow", "half-open", "backwards-utc", "backwards-monotonic"])
def test_strict_observation_projection_and_detachment(fault):
    raw = observation("run-1")
    if fault == "version":
        raw["schema_version"] = 2
    elif fault == "bool-version":
        raw["schema_version"] = True
    elif fault == "extra":
        raw["summary"] = PRIVATE
    elif fault == "domain":
        raw["clock_domain"] = "run-2"
    elif fault == "list":
        raw["active"] = []
    else:
        key = "end_utc_ns" if fault in ("half-open", "backwards-utc") else "end_monotonic_ns" if fault == "backwards-monotonic" else "start_utc_ns"
        raw["active"][key] = {"bool": True, "negative": -1, "overflow": 10 ** 21 + 1,
            "half-open": None, "backwards-utc": 89 * NS, "backwards-monotonic": 4 * NS}[fault]
    image = copy.deepcopy(raw)
    assert metrics._execution_observation(raw, "run-1") is None and raw == image
    good = observation("run-1")
    detached = metrics._execution_observation(good, "run-1")
    assert detached == good
    detached["active"].clear()
    assert good["active"] == pair()


def test_real_record_persistence_missing_prompt_retention_and_privacy(tmp_path, monkeypatch):
    root, state, _ = workspace(tmp_path)
    run = state / "runs" / "run-1"
    run.mkdir(mode=0o700)
    raw = observation(run.name)
    calls = []
    monkeypatch.setattr(metrics, "_call_provider_metrics", lambda *a, **k: calls.append(True) or {"state": "UNAVAILABLE"})
    meta = {"state": "FAILED", "execution_observation": raw, "error": PRIVATE, "summary": PRIVATE}
    first = metrics.record_cycle(root, state, run, meta, "model", "effort")
    second = metrics.record_cycle(root, state, run, meta, "model", "effort")
    assert first == second and json.loads((state / "metrics.json").read_text()) == [first]
    assert first["execution_observation"] == raw and first["prompt_sha256"] is None
    assert first["usage_state"] == "UNKNOWN" and PRIVATE not in json.dumps(first)
    raw["active"].clear()
    assert first["execution_observation"]["active"] == pair()
    (run / "prompt.txt").symlink_to(root / "absent")
    with pytest.raises((metrics.ProposalError, OSError)):
        metrics.record_cycle(root, state, run, meta, "model", "effort")
    (run / "prompt.txt").unlink()
    assert len((state / "metrics.json").read_bytes()) <= metrics.MAX_LEDGER
    assert len((run / "metrics.json").read_bytes()) <= 8000
    assert calls == [True, True]


@pytest.mark.parametrize("kind", ["quota_wait", "planned_stop"])
def test_boundary_is_open_idempotent_same_run_and_never_rereads_provider(tmp_path, monkeypatch, kind):
    root, state, _ = workspace(tmp_path)
    run = state / "runs" / "run-1"
    run.mkdir(mode=0o700)
    meta = {"state": "CONTINUE", "execution_observation": observation(run.name)}
    monkeypatch.setattr(metrics, "_call_provider_metrics", lambda *a, **k: {"state": "UNAVAILABLE"})
    metrics.record_cycle(root, state, run, meta, "m", "e")
    metrics.atomic_json(root, run / "status.json", meta, 64_000)
    monkeypatch.setattr(metrics, "_call_provider_metrics", lambda *a, **k: pytest.fail("boundary reread Provider"))
    monkeypatch.setattr(metrics, "_clock_sample", lambda: (101 * NS, 16 * NS))
    authority = [run / "status.json", run / "result.json", run / "receipt.json",
        state / "status.json", state / "ACK.json", state / "claim.json",
        root / ".local/two-site-refactor/tasks/fixture.json",
        root / ".agents/scopes/fixture.md"]
    for path in authority[1:]:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(b"immutable authority: " + path.name.encode() + b"\n")
    image = {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mode) for path in authority}
    writes, atomic = [], metrics.atomic_json
    def telemetry_only(root, path, value, limit):
        writes.append(path)
        return atomic(root, path, value, limit)
    monkeypatch.setattr(metrics, "atomic_json", telemetry_only)
    before = json.loads((run / "metrics.json").read_text())
    metrics._record_cycle_boundary(root, state, run, kind)
    after = json.loads((run / "metrics.json").read_text())
    assert {key: value for key, value in after.items() if key != "execution_observation"} == {
        key: value for key, value in before.items() if key != "execution_observation"}
    assert after["execution_observation"][kind] == {
        "start_utc_ns": 101 * NS, "start_monotonic_ns": 16 * NS,
        "end_utc_ns": None, "end_monotonic_ns": None}
    telemetry = {path: (path.read_bytes(), path.stat().st_ino) for path in (run / "metrics.json", state / "metrics.json")}
    metrics._record_cycle_boundary(root, state, run, kind)
    assert telemetry == {path: (path.read_bytes(), path.stat().st_ino) for path in telemetry}
    assert writes == [run / "metrics.json", state / "metrics.json"]
    assert json.loads((state / "metrics.json").read_text()) == [after]
    assert image == {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mode) for path in authority}
    # New process/domain cannot close the old pair by subtraction.
    wrong = copy.deepcopy(after["execution_observation"])
    wrong["clock_domain"] = "new-process-run"
    assert metrics._execution_observation(wrong, run.name) is None


@pytest.mark.parametrize("kind", ["quota_wait", "planned_stop"])
def test_real_git_immutable_root_chain_survives_telemetry_boundary(tmp_path, monkeypatch, kind):
    from tests.two_site_accepted_work_cost_support import ReportRepository, collect, whole, METRICS
    repo = ReportRepository(tmp_path / "repo")
    root, state = repo.root, repo.root / ".local/two-site-refactor/autopilot"
    run = root / repo.root_dir
    repo.specs[0]["raw"]["execution_observation"] = observation(run.name)
    repo.save_metrics()
    metrics.atomic_json(root, run / "metrics.json", repo.specs[0]["raw"], 8000)
    # All immutable Task/snapshots/Worker/Reviewer/native/Root and accepted Source
    # leaves are real fixture evidence, not fabricated accepted flags.
    paths = (set(repo.artifacts) - {METRICS}) | set(repo.source) | {
        repo.accepted_path, "docs/refactor/two-server/section-1/00-stage-ledger.md", repo.receipt_path}
    image = {path: ((root / path).read_bytes(), (root / path).stat().st_ino,
                   (root / path).stat().st_mode) for path in paths}
    before, reads = collect(repo, monkeypatch)
    whole(repo, before, reads)
    assert before["accepted_behavior_count"] == 1
    monkeypatch.setattr(metrics, "_clock_sample", lambda: (101 * NS, 16 * NS))
    monkeypatch.setattr(metrics, "_call_provider_metrics", lambda *a, **k: pytest.fail("Provider reread"))
    metrics._record_cycle_boundary(root, state, run, kind)
    assert image == {path: ((root / path).read_bytes(), (root / path).stat().st_ino,
                           (root / path).stat().st_mode) for path in paths}
    # Only the invocation-owned mutable metrics ledger has a new snapshot hash.
    repo.specs[0]["raw"] = json.loads((state / "metrics.json").read_text())[0]
    repo.artifacts[METRICS] = (state / "metrics.json").read_bytes()
    for view in repo.views:
        view.artifacts[METRICS] = repo.artifacts[METRICS]
    after, reads = collect(repo, monkeypatch)
    whole(repo, after, reads)
    assert after["accepted_behavior_count"] == 1 and after["costs"] == before["costs"]
    assert image == {path: ((root / path).read_bytes(), (root / path).stat().st_ino,
                           (root / path).stat().st_mode) for path in paths}


@pytest.mark.parametrize("boundary", ["quota_wait", "planned_stop"])
def test_actual_loop_observes_only_after_terminal_admission_or_stop(tmp_path, monkeypatch, boundary):
    root, state, codex = workspace(tmp_path)
    run = state / "runs" / "run-1"
    run.mkdir(mode=0o700)
    meta = {"state": "CONTINUE", "execution_observation": observation(run.name)}
    monkeypatch.setattr(metrics, "_call_provider_metrics", lambda *a, **k: {"state": "UNAVAILABLE"})
    metrics.record_cycle(root, state, run, meta, "m", "e")
    metrics.atomic_json(root, run / "status.json", meta, 64_000)
    before, receipt = canonical_repo("b" * 40), success("b" * 40, "a" * 40)
    calls = []
    def cycle(*args, **kwargs):
        calls.append(True)
        kwargs["before_model"](before)
        if boundary == "planned_stop":
            (state / "STOP").write_bytes(b"")
        return run, receipt
    monkeypatch.setattr(ap, "run_cycle", cycle)
    monkeypatch.setattr(ap, "validate_repository", lambda *a, **k: before)
    monkeypatch.setattr(ap, "validate_rescope_epoch_startup", lambda *a, **k: None)
    monkeypatch.setattr(ap, "prepare_rescope_attempt", lambda *a, **k: (state / "epoch", 1, None))
    monkeypatch.setattr(ap, "persisted_continuation", lambda *a, **k: None)
    monkeypatch.setattr(ap, "publish_rescope_intent", lambda *a, **k: None)
    monkeypatch.setattr(ap, "rescope_intent_exists", lambda *a, **k: True)
    monkeypatch.setattr(ap, "git", lambda *a: "b" * 40)
    monkeypatch.setattr(ap, "ref_snapshot", lambda *a: before["protected_refs"])
    monkeypatch.setattr(ap, "prune_runs", lambda *a: [])
    monkeypatch.setattr(metrics, "_clock_sample", lambda: (101 * NS, 16 * NS))
    if boundary == "quota_wait":
        from scripts import two_site_quota_supervisor_bridge as bridge
        monkeypatch.setattr(bridge, "accept_cycle_quota_disposition_locked", lambda *a, **k: {
            "state": "WAITING_QUOTA", "request_id": "request-1", "schedule": {}})
    result = ap.run_loop(root, codex, 3, 300, False, quota_wait_enabled=boundary == "quota_wait")
    assert result == (2 if boundary == "quota_wait" else 0) and calls == [True]
    final = json.loads((state / "status.json").read_text())
    assert final["state"] == ("WAITING_QUOTA" if boundary == "quota_wait" else "STOPPED")
    record = json.loads((run / "metrics.json").read_text())
    assert record["execution_observation"][boundary]["end_monotonic_ns"] is None


def test_public_signature_caps_and_legacy_summary_are_unchanged():
    assert ap.capture_bounded_process is ORIGINAL_CAPTURE
    assert inspect.getclosurevars(ap.run_cycle).globals["capture_bounded_process"] is ORIGINAL_CAPTURE
    assert list(inspect.signature(ap.run_cycle).parameters) == [
        "root", "codex", "state_root", "cycle", "timeout", "recovery", "continuation",
        "preparation_route", "before_model", "quota_original_controller_run_id"]
    assert metrics.MAX_RECORDS == 2000 and metrics.MAX_LEDGER == 2_000_000
    assert metrics.summarize_efficiency_ledger([]) == {
        "accepted_count": 0, "duplicate_count": 0, "non_accepted_count": 0, "unknown_count": 0,
        "codex": {"observed_record_count": 0, "unknown_record_count": 0,
                 "observed_subtotals": dict.fromkeys(("input_tokens", "cached_input_tokens", "uncached_input_tokens", "output_tokens"), 0)},
        "provider": {"observed_record_count": 0, "unknown_record_count": 0,
                     "observed_subtotals": dict.fromkeys(("input_tokens", "output_tokens", "total_tokens"), 0)},
        "lineages": []}
