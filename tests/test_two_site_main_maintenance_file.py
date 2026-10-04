"""Real descriptor I/O and canonical codecs; all approval metadata here is LAB only."""
import copy
import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import two_site_maintenance_transition_file as subject
from scripts.two_site_main_maintenance_record import build_main_maintenance_record, serialize_main_maintenance_record
from scripts.two_site_protected_main_approval_record import parse_protected_main_approval_record
from scripts.two_site_proposal_runner import serialize_json

LEAF = "rescope-"+"a"*64+".json"
MAIN = "refs/heads/main"


def payload(count=0):
    refs={MAIN:"a"*40,"refs/remotes/origin/main":"c"*40,"refs/heads/candidate/wa-ir-standby-v1":"d"*40}
    prior={"head":"1"*40,"status":"","protected_refs":refs,"control_blobs":{"scripts/control.py":"e"*40},
        "assignment_scope":{"assignment_id":"LAB-OLD","stage_id":"P1-00b","sha256":"f"*64,
            "expires_at":"2026-10-30T00:00:00Z","allowed_paths":["tests/new.py"],"forbidden_paths":[]}}
    current=copy.deepcopy(prior);current["head"]="3"*40;current["protected_refs"][MAIN]="b"*40
    current["assignment_scope"]["assignment_id"]="LAB-NEW"
    decision={"schema_version":"1.0","kind":"OWNER_APPROVED_PROTECTED_MAIN_CHANGE","reference":MAIN,
        "prior_main_sha":"a"*40,"approved_main_sha":"b"*40,"prior_checkpoint_sha256":"9"*64,
        "prior_head":"1"*40,"decision_id":"LAB-NOT-LIVE-APPROVAL"}
    import json
    owner=parse_protected_main_approval_record((json.dumps(decision,sort_keys=True,separators=(",",":"))+"\n").encode(),
        prior_main_sha="a"*40,approved_main_sha="b"*40,prior_checkpoint_sha256="9"*64,prior_head="1"*40)
    bindings={"prior_repository":prior,"prior_checkpoint_sha256":"9"*64,"owner_approval":owner}
    pre=[];parent=prior["head"]
    for index in range(count):
        head=hashlib.sha1(str(index).encode()).hexdigest()
        pre.append({"head":head,"parent":parent,"paths":[f"tests/test_two_site_lab{index}_description_{n:02}.py" for n in range(16)],
            "worker_run_id":f"lab-{index}-worker-0","reviewer_run_id":f"lab-{index}-reviewer-0",
            "proposal_sha256":"4"*64,"test_evidence_sha256":"5"*64})
        parent=head
    main={"head":current["head"],"parents":[parent,"b"*40],"paths":["docs/resolution.md"],
        "worker_run_id":"lab-main-worker-0","reviewer_run_id":"lab-main-reviewer-0",
        "proposal_sha256":"4"*64,"test_evidence_sha256":"5"*64}
    record=build_main_maintenance_record(**bindings,new_repository=current,pre_main_commits=pre,
        main_integration=main,post_main_commits=[])
    return serialize_main_maintenance_record(record,**bindings),bindings


@pytest.fixture
def directory(tmp_path):
    path=tmp_path/"private";path.mkdir(mode=0o700)
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:yield fd,path
    finally:os.close(fd)


@pytest.mark.parametrize("count",[0,31])
def test_actual_main_roundtrip_legacy_not_widened_and_no_overwrite(directory,count):
    fd,path=directory;raw,bindings=payload(count)
    if count:assert 16384<len(raw)<=65536
    original=copy.deepcopy(bindings)
    assert subject.read_maintenance_transition_bytes(fd,LEAF,main_bindings=bindings) is None
    subject.publish_maintenance_transition_bytes_noreplace(fd,LEAF,raw,main_bindings=bindings)
    assert subject.read_maintenance_transition_bytes(fd,LEAF,main_bindings=bindings)==raw
    assert (path/LEAF).stat().st_nlink==1 and (path/LEAF).stat().st_mode&0o777==0o600
    with pytest.raises(subject.MaintenanceTransitionDuplicateError):
        subject.publish_maintenance_transition_bytes_noreplace(fd,LEAF,raw,main_bindings=bindings)
    assert sorted(p.name for p in path.iterdir())==[LEAF] and bindings==original
    with pytest.raises(subject.MaintenanceTransitionFileError):subject.read_maintenance_transition_bytes(fd,LEAF)


@pytest.mark.parametrize("fault",["kind","owner_sha","checkpoint","prior_head","same_main","extra","callback"])
def test_invalid_binding_rejected_before_any_descriptor_io(directory,fault):
    fd,path=directory;raw,bindings=payload()
    if fault=="kind":bindings=[]
    elif fault=="owner_sha":bindings["owner_approval"]["raw_sha256"]="0"*64
    elif fault=="checkpoint":bindings["prior_checkpoint_sha256"]="0"*64
    elif fault=="prior_head":bindings["prior_repository"]["head"]="0"*40
    elif fault=="same_main":bindings["prior_repository"]["protected_refs"][MAIN]="b"*40
    elif fault=="extra":bindings["limit"]=99999999
    else:bindings["parser"]=lambda value:value
    with patch.object(subject,"_validate_transitions_fd") as stat_fd:
        for call in (lambda:subject.read_maintenance_transition_bytes(fd,LEAF,main_bindings=bindings),
                     lambda:subject.publish_maintenance_transition_bytes_noreplace(fd,LEAF,raw,main_bindings=bindings)):
            with pytest.raises(subject.MaintenanceTransitionFileError):call()
        stat_fd.assert_not_called()
    assert not list(path.iterdir())


@pytest.mark.parametrize("fault",["symlink","hardlink","mode","special_mode","oversize","invalid_wire"])
def test_real_private_leaf_faults_fail_closed(directory,fault):
    fd,path=directory;raw,bindings=payload()
    leaf=path/LEAF;leaf.write_bytes(raw);leaf.chmod(0o600)
    if fault=="symlink":leaf.rename(path/"target");leaf.symlink_to(path/"target")
    elif fault=="hardlink":os.link(leaf,path/"alias")
    elif fault=="mode":leaf.chmod(0o644)
    elif fault=="special_mode":leaf.chmod(0o4600)
    elif fault=="oversize":leaf.write_bytes(b"x"*65537)
    else:leaf.write_bytes(raw+b" ")
    with pytest.raises(subject.MaintenanceTransitionFileError):subject.read_maintenance_transition_bytes(fd,LEAF,main_bindings=bindings)


def test_main_reader_rejects_growth_after_real_read(directory):
    fd,path=directory;raw,bindings=payload();leaf=path/LEAF
    leaf.write_bytes(raw);leaf.chmod(0o600)
    original=os.read;changed=False;requested=[];received=[]
    def growing(descriptor,size):
        nonlocal changed
        requested.append(size);value=original(descriptor,size);received.append(len(value))
        if not changed:
            changed=True
            with leaf.open("ab") as stream:stream.write(b"x"*65537)
        return value
    with patch.object(subject.os,"read",side_effect=growing):
        with pytest.raises(subject.MaintenanceTransitionFileError):subject.read_maintenance_transition_bytes(fd,LEAF,main_bindings=bindings)
    assert requested and max(requested)<=4096 and sum(received)<=65537


def test_bindings_are_detached_before_write_and_legacy_serializer_is_not_used(directory):
    fd,path=directory;raw,bindings=payload();frozen=copy.deepcopy(bindings)
    actual_write=os.write
    def mutated(descriptor,data):
        bindings["prior_checkpoint_sha256"]="0"*64
        return actual_write(descriptor,data)
    with (patch.object(subject.os,"write",side_effect=mutated),
          patch.object(subject,"parse_maintenance_transition_record",side_effect=AssertionError("legacy codec used"))):
        subject.publish_maintenance_transition_bytes_noreplace(fd,LEAF,raw,main_bindings=bindings)
    assert subject.read_maintenance_transition_bytes(fd,LEAF,main_bindings=frozen)==raw


def test_post_link_failure_is_ambiguous_without_removing_final_leaf(directory):
    fd,path=directory;raw,bindings=payload()
    with patch.object(subject,"read_maintenance_transition_bytes",side_effect=OSError("lab late error")):
        with pytest.raises(subject.MaintenanceTransitionAmbiguousError):
            subject.publish_maintenance_transition_bytes_noreplace(fd,LEAF,raw,main_bindings=bindings)
    assert (path/LEAF).read_bytes()==raw


@pytest.mark.parametrize("exception",[KeyboardInterrupt(),SystemExit(),GeneratorExit()])
def test_binding_parser_preserves_base_exception(directory,exception):
    fd,path=directory;raw,bindings=payload()
    with patch("scripts.two_site_main_maintenance_record._owner",side_effect=exception):
        with pytest.raises(type(exception)) as caught:subject.read_maintenance_transition_bytes(fd,LEAF,main_bindings=bindings)
    assert caught.value is exception and not list(path.iterdir())


def test_profile_caps_are_fixed():
    assert subject._record_profile(None)==(None,16384)
    _,bindings=payload()
    assert subject._record_profile(bindings)[1]==65536
    with pytest.raises(TypeError):subject.read_maintenance_transition_bytes(-1,LEAF,limit=999999)
