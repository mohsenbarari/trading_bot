"""Complete STOP/custody proof; no live wake or Gemini claim.

The existing history factory's external model/test outcomes are simulated.
All positive lineage/ACK publication, readers, Scope/Git/Actor validation are real.
"""
from __future__ import annotations
import os
os.environ["APP_ENV_FILE"]="config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()
import copy
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from scripts import two_site_quota_supervisor_finalization as m
from scripts import two_site_codex_autopilot as ap
from scripts.two_site_quota_final_acceptance_lineage import collect_final_acceptance_upstream_lineage_from_snapshot_locked as upstream_reader
from scripts.two_site_quota_accepted_lineage_storage import read_accepted_lineage_record_candidate as lineage_reader
from scripts.two_site_quota_final_ack_storage import read_final_ack_record_candidate as ack_reader
from tests import test_two_site_quota_finalization_real_fixture as fx
from tests import test_two_site_quota_run_cycle_finalization as cycle
from tests.test_two_site_provider_broker import broker_repo

ACTUAL=m.finalize_accepted_quota_continuation_locked
DEEP=copy.deepcopy
LSTAT=Path.lstat
L=m.publish_validated_final_acceptance_lineage_locked
A=m.publish_validated_final_ack_locked
KEYS={"root","original_controller_run_id","continuation_controller_run_id",
      "before_repository","expected_transition_result","acknowledged_at_utc"}

def fingerprint(path):
  s=LSTAT(path)
  return (s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_uid,s.st_gid,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def snapshot(root):
  result={}
  for path in root.rglob("*"):
    rel=path.relative_to(root)
    if ".git" in rel.parts or path.name=="STOP": continue
    s=LSTAT(path)
    if stat.S_ISREG(s.st_mode):
      result[rel.as_posix()]=(path.read_bytes(),fingerprint(path))
  return result

def fds():
  values={}
  for name in os.listdir("/proc/self/fd"):
    try: values[name]=os.readlink("/proc/self/fd/"+name)
    except FileNotFoundError: pass
  return values

def hooks():
  return (m.finalize_accepted_quota_continuation_locked,m.copy,Path.lstat,
          m.publish_validated_final_acceptance_lineage_locked,
          m.publish_validated_final_ack_locked,fx.finalize_accepted_quota_continuation_locked,
          m._check_stop_markers,fx.validate_result,copy.deepcopy)

class Witness:
  """One complete per-call frozen oracle retained even when the factory raises."""
  def __init__(self,stage="clean",location="root",kind="regular",fault=None):
    self.stage,self.location,self.kind,self.fault=stage,location,kind,fault
    self.calls,self.records,self.witnesses=[],{},[]
    self.phase="pre"
    self.kwargs=None
    self.injected=PermissionError("injected private STOP inspection")
    self.current_hooks=None
    self.complete,self.nested,self.enforce_stop=False,False,False
    self.bridge_returns={}
    self.actual_result=None

  def marker(self):
    root=self.kwargs["root"]
    return root/("STOP" if self.location=="root" else ".local/two-site-refactor/autopilot/STOP")

  def inject(self,stage):
    if self.stage!=stage or self.kind=="stat": return
    marker=self.marker()
    marker.parent.mkdir(parents=True,exist_ok=True)
    if self.kind=="broken": marker.symlink_to(marker.with_name("absent-owned-target"))
    else: marker.write_bytes(b"owned STOP\n")

  def stat_fault(self,path,*args,**kwargs):
    if self.kind=="stat" and self.kwargs is not None and path==self.marker() and self.phase==self.stage:
      raise self.injected
    return LSTAT(path,*args,**kwargs)

  def remember(self,name,record):
    root=self.kwargs["root"]
    path=root/".local/two-site-refactor/autopilot/runs"/self.kwargs["continuation_controller_run_id"]/name
    self.records[path]=(path.read_bytes(),fingerprint(path),DEEP(record))

  def lineage(self,*args,**kwargs):
    assert args==() and set(kwargs)==KEYS-{"acknowledged_at_utc"}
    assert kwargs=={k:self.kwargs[k] for k in KEYS-{"acknowledged_at_utc"}}
    self.calls.append("lineage")
    if self.fault=="lineage": raise self.injected
    record=L(*args,**kwargs)
    self.bridge_returns["lineage"]=record
    self.remember("accepted-lineage.json",record)
    self.phase="lineage"
    self.inject("lineage")
    return record

  def ack(self,*args,**kwargs):
    assert args==()
    expected={k:self.kwargs[k] for k in KEYS-{"before_repository"}}
    expected["accepted_lineage_record"]=next(v[2] for p,v in self.records.items() if p.name=="accepted-lineage.json")
    assert kwargs==expected
    self.calls.append("ack")
    if self.fault in ("ack","base"): raise self.injected
    record=A(*args,**kwargs)
    self.bridge_returns["ack"]=record
    self.remember("final-ack.json",record)
    self.phase="ack"
    self.inject("ack")
    if self.nested:record["owned_nested_copy_contract"]={"items":[["retained"]]}
    return record

  def copy_fault(self,value):
    assert self.phase=="ack" and any(p.name=="final-ack.json" for p in self.records)
    raise self.injected

  def readers(self):
    if not self.records: return
    root=self.kwargs["root"]
    before=self.kwargs["before_repository"]
    result=self.kwargs["expected_transition_result"]
    scope=before["assignment_scope"]
    context={"controller_run_id":self.kwargs["continuation_controller_run_id"],
      "protected_refs":before["protected_refs"],"assignment_id":scope.assignment_id,
      "assignment_sha256":scope.sha256,"current_head":result["head_after"]}
    upstream=upstream_reader(root,self.kwargs["original_controller_run_id"])
    line=lineage_reader(root,self.kwargs["continuation_controller_run_id"],result,context,upstream)
    assert line==next(v[2] for p,v in self.records.items() if p.name=="accepted-lineage.json")
    if any(p.name=="final-ack.json" for p in self.records):
      ack=ack_reader(root,self.kwargs["original_controller_run_id"],
                     self.kwargs["continuation_controller_run_id"],result,context,upstream)
      assert ack==next(v[2] for p,v in self.records.items() if p.name=="final-ack.json")

  def verify(self,inputs,prior,descriptors,bindings):
    root=self.kwargs["root"]
    assert self.kwargs==inputs
    after=snapshot(root)
    assert {name:after[name] for name in prior}==prior
    allowed={p.relative_to(root).as_posix() for p in self.records}
    assert set(after)-set(prior)==allowed
    for path,(raw,identity,record) in self.records.items():
      assert path.read_bytes()==raw and fingerprint(path)==identity
      assert json.loads(raw)==record
      assert stat.S_IMODE(identity[2])==0o600 and identity[3]==1
    self.readers()
    assert snapshot(root)==after
    for path,(raw,identity,record) in self.records.items():
      assert path.read_bytes()==raw and fingerprint(path)==identity
    assert fds()==descriptors and hooks()==bindings
    self.witnesses.append((DEEP(inputs),prior,DEEP(self.records),descriptors,bindings))

  def subject(self,**kwargs):
    assert set(kwargs)==KEYS
    if self.kwargs is not None:assert kwargs==self.kwargs
    self.kwargs=kwargs
    self.calls,self.records,self.bridge_returns=[],{},{}
    self.phase="pre"
    root=kwargs["root"]
    assert root.is_absolute() and root.resolve()==root
    os.chmod(root,0o700)
    assert stat.S_IMODE(LSTAT(root).st_mode)==0o700
    self.inject("pre")
    with ExitStack() as stack:
      stack.enter_context(patch.object(m,"publish_validated_final_acceptance_lineage_locked",self.lineage))
      stack.enter_context(patch.object(m,"publish_validated_final_ack_locked",self.ack))
      if self.kind=="stat":
        stack.enter_context(patch.object(Path,"lstat",autospec=True,side_effect=self.stat_fault))
      if self.fault=="copy":
        stack.enter_context(patch.object(m,"copy",SimpleNamespace(deepcopy=self.copy_fault)))
      inputs,prior=DEEP(kwargs),snapshot(root)
      descriptors,bindings=fds(),hooks()
      error=None
      try:
        result=ACTUAL(**kwargs)
        self.actual_result=result
        if self.nested:
          return {k:v for k,v in result.items() if k!="owned_nested_copy_contract"}
        return result
      except BaseException as exc:
        error=exc
        raise
      finally:
        self.verify(inputs,prior,descriptors,bindings)
        if self.enforce_stop:
          assert type(error) is m.SupervisorFinalizationError
          assert str(error)=="quota finalization stopped"
          assert self.calls==[] and self.records=={}


  def run(self,broker):
    original=hooks()
    try:
      with ExitStack() as stack:
        stack.enter_context(patch.object(fx,"finalize_accepted_quota_continuation_locked",self.subject))
        if self.complete:
          def external_complete(raw):
            controlled=DEEP(raw)
            assert controlled["state"]=="CONTINUE" and controlled["commit"] is not None
            controlled.update(state="COMPLETE",next_action=None)
            return ap.validate_result(controlled)
          stack.enter_context(patch.object(fx,"validate_result",external_complete))
        return fx._build_real_finalized_history_fixture(broker,candidate_runner=fx.simulated_candidate_runner)
    finally:
      assert hooks()==original
      if self.kwargs is not None:
        marker=self.marker()
        try: LSTAT(marker)
        except FileNotFoundError: pass
        else: marker.unlink()

  def topology(self,names):
    root=self.kwargs["root"]
    target=root/".local/two-site-refactor/autopilot/runs"/self.kwargs["continuation_controller_run_id"]
    assert {n for n in ("accepted-lineage.json","final-ack.json") if (target/n).exists()}==set(names)
    assert self.witnesses
    self.readers()

@pytest.mark.parametrize("stage",["pre","lineage","ack"])
@pytest.mark.parametrize("location",["root","ap"])
@pytest.mark.parametrize("kind",["regular","broken","stat"])
def test_real_stop_boundaries_complete_custody(broker_repo,stage,location,kind):
  w=Witness(stage,location,kind)
  error_type=m.SupervisorFinalizationError if stage=="pre" else m.SupervisorFinalizationAmbiguousError
  with pytest.raises(error_type) as caught: w.run(broker_repo)
  assert type(caught.value) is error_type
  if kind=="stat":
    assert caught.value.__cause__ is w.injected
    if stage=="pre":assert str(caught.value)=="STOP marker inspection failed"
  elif stage=="pre":assert str(caught.value)=="quota finalization stopped"
  elif stage!="pre":
    assert type(caught.value.__cause__) is m.SupervisorFinalizationError
    assert str(caught.value.__cause__)=="quota finalization stopped"
  assert w.calls=={"pre":[],"lineage":["lineage"],"ack":["lineage","ack"]}[stage]
  w.topology({"pre":[],"lineage":["accepted-lineage.json"],"ack":["accepted-lineage.json","final-ack.json"]}[stage])

@pytest.mark.parametrize("state",["CONTINUE","COMPLETE"])
def test_clean_real_outcomes_and_nested_copy(broker_repo,state):
  w=Witness()
  w.complete=state=="COMPLETE"
  w.nested=True  # Controlled copy sentinel only AFTER actual durable ACK/readback.
  result=w.run(broker_repo)
  assert w.calls==["lineage","ack"] and result["ack"]["accepted_state"]==state
  w.topology(["accepted-lineage.json","final-ack.json"])
  actual=w.bridge_returns["ack"]
  copied=w.actual_result
  assert copied==actual and copied is not actual
  assert copied["owned_nested_copy_contract"] is not actual["owned_nested_copy_contract"]
  assert copied["owned_nested_copy_contract"]["items"] is not actual["owned_nested_copy_contract"]["items"]
  assert copied["owned_nested_copy_contract"]["items"][0] is not actual["owned_nested_copy_contract"]["items"][0]
  copied["owned_nested_copy_contract"]["items"][0].append("changed")
  assert actual["owned_nested_copy_contract"]=={"items":[["retained"]]}
  w.readers()

@pytest.mark.parametrize("fault",["lineage","ack","base","copy"])
def test_original_exception_priority_and_private_return_copy(broker_repo,fault):
  w=Witness(fault=fault)
  if fault=="base": w.injected=KeyboardInterrupt("owned ACK signal")
  original_copy=copy.deepcopy
  expected=PermissionError if fault=="lineage" else m.SupervisorFinalizationAmbiguousError
  with pytest.raises(expected) as caught:w.run(broker_repo)
  assert copy.deepcopy is original_copy
  assert caught.value is w.injected if fault=="lineage" else caught.value.__cause__ is w.injected
  assert w.calls==["lineage"] if fault=="lineage" else w.calls==["lineage","ack"]
  names=[] if fault=="lineage" else ["accepted-lineage.json"]
  if fault=="copy": names.append("final-ack.json")
  w.topology(names)

def test_reached_same_stop_oracle_guard_disabled_then_restored(broker_repo):
  w=Witness("pre","ap")
  w.enforce_stop=True
  with pytest.raises(m.SupervisorFinalizationError):w.run(broker_repo)
  kwargs=DEEP(w.kwargs)
  marker=w.marker()
  original_hooks=hooks()
  try:
    with ap.exclusive_supervisor_lock(kwargs["root"]/".local/two-site-refactor/autopilot/autopilot.lock"):
      with patch.object(m,"_check_stop_markers",lambda root:None):
        with pytest.raises(AssertionError):w.subject(**kwargs)
      assert w.calls==["lineage","ack"] and w.actual_result["accepted_state"]=="CONTINUE"
      mutant_records=DEEP(w.records)
      frozen=snapshot(kwargs["root"])
      assert set(p.name for p in mutant_records)=={"accepted-lineage.json","final-ack.json"}
      assert hooks()==original_hooks
      with pytest.raises(m.SupervisorFinalizationError):w.subject(**kwargs)
      assert w.calls==[] and w.records=={}
      assert snapshot(kwargs["root"])==frozen
      w.records=mutant_records
      w.readers()
      assert snapshot(kwargs["root"])==frozen
      assert len(w.witnesses)==3
  finally:
    assert hooks()==original_hooks
    try:LSTAT(marker)
    except FileNotFoundError:pass
    else:marker.unlink()
  # Real late publications remain; restored STOP never re-enters either publisher.

@pytest.mark.parametrize("location",["root","ap"])
def test_actual_run_cycle_stop_and_existing_checkpoint_custody(tmp_path,location):
  root,codex,state_root=cycle._setup_workspace(tmp_path)
  os.chmod(root,0o700)
  before=cycle._make_before()
  checkpoint=state_root/"accepted_checkpoint.json"
  checkpoint.parent.mkdir(parents=True,exist_ok=True)
  checkpoint.write_bytes(b'{"owned_previous_checkpoint":"retained"}\n')
  saved=(checkpoint.read_bytes(),fingerprint(checkpoint))
  marker=root/("STOP" if location=="root" else ".local/two-site-refactor/autopilot/STOP")
  marker.parent.mkdir(parents=True,exist_ok=True)
  marker.write_bytes(b"owned STOP\n")
  calls,seen=[],[]
  def capture(process,input_bytes,out,err,timeout,max_output=0):
    calls.append("controlled Codex process")
    (out.parent/"result.json").write_text(json.dumps(cycle._make_raw_result(state="CONTINUE")))
  def finalizer(**kwargs):
    seen.append(DEEP(kwargs))
    frozen=DEEP(kwargs)
    fd,bindings=fds(),hooks()
    prior=snapshot(root)
    try:return ACTUAL(**kwargs)
    finally:
      assert kwargs==frozen and snapshot(root)==prior
      assert fds()==fd and hooks()==bindings
  original_hooks=hooks()
  try:
    with cycle._mock_cycle_environment(codex,validate_repo_side_effect=lambda *a,**kw:before,capture_side_effect=capture),\
         patch.object(m,"finalize_accepted_quota_continuation_locked",finalizer):
      with pytest.raises(ap.QuotaFinalizationHardStopError) as caught:
        ap.run_cycle(root,codex,state_root,1,300,quota_original_controller_run_id=cycle.VALID_ORIGINAL_RUN_ID)
    assert type(caught.value.__cause__) is m.SupervisorFinalizationError
    assert str(caught.value.__cause__)=="quota finalization stopped"
    assert len(seen)==1 and calls==["controlled Codex process"]
    assert stat.S_IMODE(LSTAT(root).st_mode)==0o700
    assert checkpoint.read_bytes()==saved[0] and fingerprint(checkpoint)==saved[1]
    runs=list((state_root/"runs").iterdir())
    assert len(runs)==1 and json.loads((runs[0]/"status.json").read_text())["state"]=="FAILED"
    assert not (runs[0]/"accepted-lineage.json").exists() and not (runs[0]/"final-ack.json").exists()
  finally:
    assert hooks()==original_hooks
    marker.unlink()
