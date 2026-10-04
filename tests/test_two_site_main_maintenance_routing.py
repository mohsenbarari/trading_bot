"""Actual LAB files/Git/Broker/owner/CP/evidence; never live acceptance data."""
from contextlib import contextmanager
import copy
import fcntl
import os
from unittest.mock import patch

import pytest

from scripts import two_site_main_maintenance_storage as main
from scripts import two_site_maintenance_transition_storage as legacy
from scripts import two_site_maintenance_transition_startup as startup
from scripts import two_site_quarantine_recovery_storage as quarantine
from scripts.two_site_proposal_runner import serialize_json, unique_json
from tests.test_two_site_main_maintenance_storage import prepare, publish, leaf, private, git


def no_fallback(monkeypatch):
    calls = []
    def forbidden(*args):
        calls.append(args)
        raise AssertionError("main failure must not reach quarantine")
    monkeypatch.setattr(quarantine, "read_quarantine_recovery_locked", forbidden)
    return calls


def actual_startup(root, state, monkeypatch):
    monkeypatch.setattr(startup, "validate_repository", main.validate_repository)
    repository = main.validate_repository(root, require_clean=True)
    startup.require_reviewed_maintenance_transition_locked(root, state, repository)


def test_real_main_route_exits_old_hold_before_fresh_continuous_proof(tmp_path, monkeypatch):
    root, state, record, bindings = prepare(tmp_path, monkeypatch)
    publish(root, state, record)
    before = (git(root, "show-ref"), (state / "accepted_checkpoint.json").read_bytes(), leaf(state, record).read_bytes())
    events, held = [], []
    original = legacy.hold_idle_broker_client_lock_for_cold_adoption
    @contextmanager
    def tracked(label, root_arg):
        assert not held, "Broker contexts must never nest"
        events.append(label + ":enter")
        with original(root_arg):
            held.append(label)
            try:
                yield
            finally:
                held.pop()
        events.append(label + ":exit")
    monkeypatch.setattr(legacy, "hold_idle_broker_client_lock_for_cold_adoption", lambda r: tracked("old", r))
    monkeypatch.setattr(main, "hold_idle_broker_client_lock_for_cold_adoption", lambda r: tracked("main", r))
    real_proof = main.verify_main_maintenance_evidence_locked
    def proof(*args, **kwargs):
        assert held == ["main"]
        descriptor = os.open(root / ".local/two-site-refactor/provider-broker/client.lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
        return real_proof(*args, **kwargs)
    monkeypatch.setattr(main, "verify_main_maintenance_evidence_locked", proof)
    assert legacy.read_maintenance_transition_locked(root, state) == record
    assert events == ["old:enter", "old:exit", "main:enter", "main:exit"]
    fallback = no_fallback(monkeypatch)
    actual_startup(root, state, monkeypatch)
    assert not fallback
    assert before == (git(root, "show-ref"), (state / "accepted_checkpoint.json").read_bytes(), leaf(state, record).read_bytes())


@pytest.mark.parametrize("fault", ["missing", "record", "owner", "actor", "same_head"])
def test_known_main_failure_is_typed_and_never_quarantine(tmp_path, monkeypatch, fault):
    root, state, record, bindings = prepare(tmp_path, monkeypatch)
    if fault != "missing":
        publish(root, state, record)
    if fault == "record":
        leaf(state, record).write_bytes(b"{}")
    elif fault == "owner":
        (state / "protected-main-approval.json").write_bytes(b"{}")
    elif fault == "actor":
        entry = record["pre_main_commits"][0]
        path = root / ".local/two-site-refactor/proposals" / entry["reviewer_run_id"] / "result.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif fault == "same_head":
        # Only the generated LAB repository: keep its index/worktree consistent
        # with the test ref so the intended head fence, not dirty-Git, is tested.
        assert root.is_relative_to(tmp_path)
        git(root, "update-ref", "refs/heads/refactor/two-site-architecture", bindings["prior_repository"]["head"])
        git(root, "read-tree", "--reset", "-u", bindings["prior_repository"]["head"])
    fallback = no_fallback(monkeypatch)
    with pytest.raises(legacy.MainMaintenanceRouteError) as caught:
        legacy.read_maintenance_transition_locked(root, state)
    assert str(caught.value) == "main maintenance verification failed"
    assert caught.value.__cause__ is None
    with pytest.raises(startup.MaintenanceTransitionStartupError, match="main maintenance verification failed"):
        actual_startup(root, state, monkeypatch)
    assert not fallback


def test_foreign_ref_mismatch_never_selects_main_reader(tmp_path, monkeypatch):
    root, state, record, bindings = prepare(tmp_path, monkeypatch)
    git(root, "update-ref", "refs/remotes/origin/main", record["new_repository"]["head"])
    def forbidden(*args):
        raise AssertionError("foreign ref change cannot select main reader")
    monkeypatch.setattr(main, "read_main_maintenance_transition_locked", forbidden)
    with pytest.raises(legacy.MaintenanceTransitionStorageError) as caught:
        legacy.read_maintenance_transition_locked(root, state)
    assert not isinstance(caught.value, legacy.MainMaintenanceRouteError)
    assert "Protected refs mismatch" in str(caught.value)


def test_old_broker_exit_failure_is_main_hard_failure(tmp_path, monkeypatch):
    root, state, record, bindings = prepare(tmp_path, monkeypatch)
    original = legacy.hold_idle_broker_client_lock_for_cold_adoption
    @contextmanager
    def late_failure(root_arg):
        with original(root_arg):
            yield
            path = root / ".local/two-site-refactor/provider-broker/status.json"
            value = unique_json(path.read_text()); value["state"] = "RUNNING"
            private(path, serialize_json(value))
    monkeypatch.setattr(legacy, "hold_idle_broker_client_lock_for_cold_adoption", late_failure)
    def forbidden(*args):
        raise AssertionError("unsuccessful old exit cannot start new hold")
    monkeypatch.setattr(main, "read_main_maintenance_transition_locked", forbidden)
    fallback = no_fallback(monkeypatch)
    with pytest.raises(startup.MaintenanceTransitionStartupError, match="main maintenance verification failed"):
        actual_startup(root, state, monkeypatch)
    assert not fallback


@pytest.mark.parametrize("fault", ["stop", "owner", "checkpoint", "foreign_ref"])
def test_drift_between_holds_is_rechecked_by_actual_main_reader(tmp_path, monkeypatch, fault):
    root, state, record, bindings = prepare(tmp_path, monkeypatch)
    publish(root, state, record)
    original = legacy.hold_idle_broker_client_lock_for_cold_adoption
    @contextmanager
    def late_stop(root_arg):
        with original(root_arg):
            yield
        if fault == "stop":
            (state / "STOP").write_bytes(b"")
        elif fault == "owner":
            (state / "protected-main-approval.json").write_bytes(b"{}")
        elif fault == "checkpoint":
            (state / "accepted_checkpoint.json").write_bytes(b"{}")
        else:
            git(root, "update-ref", "refs/remotes/origin/main", record["new_repository"]["head"])
    monkeypatch.setattr(legacy, "hold_idle_broker_client_lock_for_cold_adoption", late_stop)
    fallback = no_fallback(monkeypatch)
    with pytest.raises(startup.MaintenanceTransitionStartupError, match="main maintenance verification failed"):
        actual_startup(root, state, monkeypatch)
    assert not fallback
    assert leaf(state, record).exists()


@pytest.mark.parametrize("returned", [None, [], "not a record"])
def test_nonrecord_main_result_cannot_trigger_fallback(tmp_path, monkeypatch, returned):
    root, state, record, bindings = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "read_main_maintenance_transition_locked", lambda *args: copy.deepcopy(returned))
    fallback = no_fallback(monkeypatch)
    with pytest.raises(startup.MaintenanceTransitionStartupError, match="main maintenance verification failed"):
        actual_startup(root, state, monkeypatch)
    assert not fallback


def test_main_base_exception_propagates_without_fallback(tmp_path, monkeypatch):
    root, state, record, bindings = prepare(tmp_path, monkeypatch)
    class Interrupted(BaseException):
        pass
    interrupted = Interrupted()
    def stop(*args):
        raise interrupted
    monkeypatch.setattr(main, "read_main_maintenance_transition_locked", stop)
    fallback = no_fallback(monkeypatch)
    with pytest.raises(Interrupted) as caught:
        actual_startup(root, state, monkeypatch)
    assert caught.value is interrupted
    assert not fallback
