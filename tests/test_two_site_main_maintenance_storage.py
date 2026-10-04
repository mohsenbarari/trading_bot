"""Actual temporary Git/Broker/files/owner/checkpoint/evidence; LAB sessions only.

Only current shared-Assignment repository discovery is supplied by the lab oracle
and the lab control set is one real blob. All storage/custody/codecs/readers and
the complete historical evidence and Git gates remain the actual implementations.
These fixtures are never live owner, Provider or acceptance evidence.
"""
import copy
import fcntl
import hashlib
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import two_site_main_maintenance_storage as subject
from scripts import two_site_codex_autopilot as autopilot
from scripts import two_site_maintenance_transition_storage as legacy
from scripts.two_site_main_maintenance_record import build_main_maintenance_record, serialize_main_maintenance_record
from scripts.two_site_proposal_runner import serialize_json, unique_json
from scripts.two_site_protected_main_approval_record import parse_protected_main_approval_record
from scripts.two_site_main_maintenance_evidence import verify_main_maintenance_evidence_locked
from tests.test_two_site_main_maintenance_evidence import fixture as graph_fixture, git, CONTROL


def private(path, raw):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    path.write_bytes(raw);path.chmod(0o600)


def prepare(tmp_path, monkeypatch):
    root,record,bindings=graph_fixture(tmp_path)
    state=root/subject.STATE
    state.mkdir(parents=True,mode=0o700)
    for directory in (root/".local",root/".local/two-site-refactor",state):directory.chmod(0o700)
    (state/"runs").mkdir(mode=0o700)
    broker=root/".local/two-site-refactor/provider-broker";broker.mkdir(exist_ok=True,mode=0o700);broker.chmod(0o700)
    for name in ("requests","claimed"):(broker/name).mkdir(mode=0o700)
    private(broker/"client.lock",b"")
    private(broker/"status.json",serialize_json({"schema_version":"1.0","state":"IDLE","pid":12345,
        "started_at":time.time()-100,"updated_at":time.time()}))
    monkeypatch.setattr(autopilot,"CONTROL_PATHS",(Path(CONTROL),))
    prior=bindings["prior_repository"]
    prior_raw=copy.deepcopy(prior);prior_raw["assignment_scope"]=autopilot.AssignmentScope(**prior["assignment_scope"])
    receipt={"schema_version":"1.0","state":"CONTINUE","disposition":"NONE","category":"NONE",
        "summary":"LAB bootstrap receipt, never a live accepted cycle","commit":prior["head"],
        "next_action":"LAB only","blockers":[],"gemini_worker_run_id":"lab-initial-worker-0",
        "gemini_reviewer_run_id":"lab-initial-reviewer-0","proposal_sha256":"0"*64,
        "head_before":"0"*40,"head_after":prior["head"],"head_changed":True}
    checkpoint=autopilot.serialize_accepted_checkpoint(receipt,prior_raw)
    envelope=unique_json(checkpoint.decode())
    assert type(envelope["schema_version"]) is int and envelope["schema_version"]==1
    assert envelope["result"]["schema_version"]=="1.0"
    private(state/"accepted_checkpoint.json",checkpoint)
    checkpoint_sha=hashlib.sha256(checkpoint).hexdigest()
    decision=copy.deepcopy(bindings["owner_approval"]["decision"])
    decision["prior_checkpoint_sha256"]=checkpoint_sha
    import json
    owner_raw=(json.dumps(decision,sort_keys=True,separators=(",",":"))+"\n").encode()
    private(state/"protected-main-approval.json",owner_raw)
    owner=parse_protected_main_approval_record(owner_raw,prior_main_sha=prior["protected_refs"][subject.MAIN],
        approved_main_sha=record["new_repository"]["protected_refs"][subject.MAIN],
        prior_checkpoint_sha256=checkpoint_sha,prior_head=prior["head"])
    bindings={"prior_repository":prior,"prior_checkpoint_sha256":checkpoint_sha,"owner_approval":owner}
    record=build_main_maintenance_record(**bindings,new_repository=record["new_repository"],
        pre_main_commits=record["pre_main_commits"],main_integration=record["main_integration"],post_main_commits=record["post_main_commits"])
    current=record["new_repository"]
    def repository_oracle(root_arg,require_clean):
        assert root_arg==root and require_clean
        assert not git(root,"status","--porcelain=v1","--untracked-files=all")
        value=copy.deepcopy(current)
        value["head"]=git(root,"rev-parse","HEAD")
        value["protected_refs"]={ref:git(root,"rev-parse",ref) for ref in current["protected_refs"]}
        value["control_blobs"]={CONTROL:git(root,"rev-parse","HEAD:"+CONTROL)}
        value["assignment_scope"]=autopilot.AssignmentScope(**current["assignment_scope"])
        return value
    monkeypatch.setattr(subject,"validate_repository",repository_oracle)
    monkeypatch.setattr(legacy,"validate_repository",repository_oracle)
    return root,state,record,bindings


def publish(root,state,record):
    return subject.publish_main_maintenance_transition_locked(root,state,
        **{key:record[key] for key in ("pre_main_commits","main_integration","post_main_commits")})


def leaf(state,record):
    return state/"maintenance-transitions"/(autopilot.rescope_epoch_basename(record["new_repository"])+".json")


def test_actual_full_file_owner_checkpoint_git_evidence_and_continuous_broker_custody(tmp_path,monkeypatch):
    root,state,record,bindings=prepare(tmp_path,monkeypatch)
    before=(git(root,"show-ref"),git(root,"status","--porcelain=v1"),(state/"accepted_checkpoint.json").read_bytes())
    checks=[]
    def actual_evidence(*args,**kwargs):
        fd=os.open(root/".local/two-site-refactor/provider-broker/client.lock",os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(fd)
        checks.append(copy.deepcopy(kwargs))
        return verify_main_maintenance_evidence_locked(*args,**kwargs)
    with patch.object(subject,"verify_main_maintenance_evidence_locked",side_effect=actual_evidence):
        assert publish(root,state,record)==record
        assert subject.read_main_maintenance_transition_locked(root,state)==record
    assert len(checks)==3 and all(value==bindings for value in checks)
    assert leaf(state,record).read_bytes()==serialize_main_maintenance_record(record,**bindings)
    assert before==(git(root,"show-ref"),git(root,"status","--porcelain=v1"),(state/"accepted_checkpoint.json").read_bytes())
    with pytest.raises(subject.MainMaintenanceStorageError):publish(root,state,record)


@pytest.mark.parametrize("fault",["owner","checkpoint","broker","queue","stop_root","stop_state","pending","source","main_ref","foreign_ref"])
def test_real_prepublication_faults_leave_no_transition(tmp_path,monkeypatch,fault):
    root,state,record,bindings=prepare(tmp_path,monkeypatch)
    broker=root/".local/two-site-refactor/provider-broker"
    if fault=="owner":(state/"protected-main-approval.json").write_bytes(b"{}")
    elif fault=="checkpoint":(state/"accepted_checkpoint.json").write_bytes(b"{}")
    elif fault=="broker":
        value=unique_json((broker/"status.json").read_text());value["state"]="RUNNING"
        private(broker/"status.json",serialize_json(value))
    elif fault=="queue":private(broker/"requests"/"foreign.json",b"{}")
    elif fault.startswith("stop"):(root if fault=="stop_root" else state).joinpath("STOP").write_bytes(b"")
    elif fault=="pending":
        run=state/"runs/cycle-0001-20261004T000000Z-1";run.mkdir(mode=0o700)
        private(run/"quota-resume-claim.json",b"{}")
    elif fault=="source":
        entry=record["pre_main_commits"][0]
        path=root/f".local/two-site-refactor/proposals/{entry['worker_run_id']}/task.json"
        value=unique_json(path.read_text());value["sources"]["scripts/two_site_worker.py"]["content"]="fake"
        private(path,serialize_json(value))
    else:git(root,"update-ref",subject.MAIN if fault=="main_ref" else "refs/remotes/origin/main",
        record["prior_head"] if fault=="main_ref" else record["new_repository"]["head"])
    with pytest.raises(subject.MainMaintenanceStorageError) as caught:publish(root,state,record)
    assert str(caught.value)==subject.ERROR and caught.value.__cause__ is None
    assert not (state/"maintenance-transitions").exists()


@pytest.mark.parametrize("fault",["missing","extra","wire","mode","hardlink","symlink"])
def test_real_reader_faults_never_return_none_or_quarantine_fallback(tmp_path,monkeypatch,fault):
    root,state,record,bindings=prepare(tmp_path,monkeypatch)
    publish(root,state,record);path=leaf(state,record)
    if fault=="missing":path.unlink()
    elif fault=="extra":private(path.parent/("rescope-"+"a"*64+".json"),path.read_bytes())
    elif fault=="wire":path.write_bytes(path.read_bytes()+b" ")
    elif fault=="mode":path.chmod(0o644)
    elif fault=="hardlink":os.link(path,path.parent/"alias")
    else:path.rename(path.parent/"other");path.symlink_to(path.parent/"other")
    with pytest.raises(subject.MainMaintenanceStorageError):subject.read_main_maintenance_transition_locked(root,state)


@pytest.mark.parametrize("fault",["stop","owner","checkpoint","main","broker","leaf","directory","read_failure"])
def test_real_postpublication_faults_are_ambiguous_no_replay_or_deletion(tmp_path,monkeypatch,fault):
    root,state,record,bindings=prepare(tmp_path,monkeypatch)
    actual=subject.publish_maintenance_transition_bytes_noreplace
    def disrupted(*args,**kwargs):
        actual(*args,**kwargs)
        if fault=="stop":(state/"STOP").write_bytes(b"")
        elif fault=="owner":(state/"protected-main-approval.json").write_bytes(b"{}")
        elif fault=="checkpoint":(state/"accepted_checkpoint.json").write_bytes(b"{}")
        elif fault=="main":git(root,"update-ref",subject.MAIN,record["prior_head"])
        elif fault=="broker":
            path=root/".local/two-site-refactor/provider-broker/status.json"
            value=unique_json(path.read_text());value["state"]="RUNNING";private(path,serialize_json(value))
        elif fault=="leaf":leaf(state,record).write_bytes(b"{}")
        elif fault=="directory":leaf(state,record).parent.rename(state/"retained-detached-transition")
        else:raise OSError("LAB post-link error")
    with patch.object(subject,"publish_maintenance_transition_bytes_noreplace",side_effect=disrupted):
        with pytest.raises(subject.MaintenanceTransitionAmbiguousError) as caught:publish(root,state,record)
    assert str(caught.value)=="main transition publication is ambiguous"
    assert list(state.glob("**/rescope-*.json"))


def test_late_artifact_change_rejected_by_actual_postpublication_evidence(tmp_path,monkeypatch):
    root,state,record,bindings=prepare(tmp_path,monkeypatch)
    actual=subject.publish_maintenance_transition_bytes_noreplace
    def disrupted(*args,**kwargs):
        actual(*args,**kwargs)
        entry=record["main_integration"]
        (root/f".local/two-site-refactor/proposals/{entry['reviewer_run_id']}/result.json").write_bytes(b"{}")
    with patch.object(subject,"publish_maintenance_transition_bytes_noreplace",side_effect=disrupted):
        with pytest.raises(subject.MaintenanceTransitionAmbiguousError):publish(root,state,record)
    assert leaf(state,record).exists()


@pytest.mark.parametrize("fault",["stop","owner","checkpoint","main","broker","directory"])
def test_actual_reader_late_boundary_faults_cannot_return_success(tmp_path,monkeypatch,fault):
    root,state,record,bindings=prepare(tmp_path,monkeypatch)
    publish(root,state,record)
    actual=subject._read
    def disrupted(*args,**kwargs):
        value=actual(*args,**kwargs)
        if fault=="stop":(state/"STOP").write_bytes(b"")
        elif fault=="owner":(state/"protected-main-approval.json").write_bytes(b"{}")
        elif fault=="checkpoint":(state/"accepted_checkpoint.json").write_bytes(b"{}")
        elif fault=="main":git(root,"update-ref",subject.MAIN,record["prior_head"])
        elif fault=="directory":leaf(state,record).parent.rename(state/"retained-detached-transition")
        else:
            path=root/".local/two-site-refactor/provider-broker/status.json"
            status=unique_json(path.read_text());status["state"]="RUNNING";private(path,serialize_json(status))
        return value
    with patch.object(subject,"_read",side_effect=disrupted):
        with pytest.raises(subject.MainMaintenanceStorageError):subject.read_main_maintenance_transition_locked(root,state)
    assert list(state.glob("**/rescope-*.json"))


def test_invalid_root_rejected_before_broker_and_base_preserved(tmp_path):
    with patch.object(subject,"hold_idle_broker_client_lock_for_cold_adoption") as broker:
        with pytest.raises(subject.MainMaintenanceStorageError):subject.read_main_maintenance_transition_locked("bad",tmp_path)
        broker.assert_not_called()
    for exception in (KeyboardInterrupt(),SystemExit(),GeneratorExit()):
        with patch.object(subject,"_pin_validated_root",side_effect=exception):
            # Test the shared guard directly without changing live I/O contracts.
            from contextlib import nullcontext
            with patch.object(subject,"hold_idle_broker_client_lock_for_cold_adoption",return_value=nullcontext()):
                with pytest.raises(type(exception)) as caught:subject.read_main_maintenance_transition_locked(tmp_path,tmp_path/subject.STATE)
            assert caught.value is exception
