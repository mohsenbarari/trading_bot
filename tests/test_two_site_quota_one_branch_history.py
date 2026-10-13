"""Native quota ONE history join; only local Provider/quota plumbing is simulated.

No fake test process, Report, receipt, evidence reader or binding. Native children
exercise the actual branch gate on owned temporary Git metadata. Not live replay.
"""
from __future__ import annotations
import os
os.environ["APP_ENV_FILE"]="config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()
import ast
import copy
import hashlib
import inspect
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from scripts import two_site_proposal_pipeline as p
from scripts import two_site_proposal_runner as r
from scripts import two_site_pre_review_receipt as receipt_mod
from scripts import two_site_quota_resume_worker_correction_context as context_mod
from scripts import two_site_quota_resume_post_worker_one as post_mod
from scripts import two_site_quota_resume_post_worker_one_remainder as one
from scripts.two_site_quota_resume_worker_evidence import load_worker_correction_evidence
from tests.test_two_site_failure_feedback_transport import full,repo,native_route,git_fixture

PATH="tests/test_existing.py"
BRANCH="scripts.two_site_codex_autopilot.AutopilotError: autopilot requires branch refactor/two-site-architecture"
FACTOR=hashlib.sha256(b'{"branch":"refactor/two-site-architecture","factor":"repository_branch","selectors":["tests/test_existing.py"]}').hexdigest()
BRANCH_SOURCE="""from pathlib import Path
import shutil, subprocess, tempfile
from scripts import two_site_proposal_runner as runner
from scripts.two_site_codex_autopilot import validate_repository

def test_a():
    with tempfile.TemporaryDirectory() as td:
        wrong = Path(td)
        canonical = Path(runner.__file__).resolve().parents[1]
        for rel in (runner.GEMINI_RUNTIME_CONTRACT_REL, *runner.GEMINI_RUNTIME_REFERENCE_PATHS):
            dest = wrong / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(canonical / rel, dest)
        for args in (["init", "-q", "-b", "wrong-fixture-branch"],
                     ["config", "user.name", "Quota Branch Fixture"],
                     ["config", "user.email", "quota-branch@test.invalid"]):
            subprocess.run(["git", "-C", str(wrong), *args], check=True)
        validate_repository(wrong, require_clean=False)
        assert 1 == 2
"""
OTHER="def test_b():\n    assert 0 == 1\n"
PASS="def test_c():\n    assert 2 == 2\n"
RAW_ONLY="def test_a():\n    print('E "+BRANCH+"')\n    assert 0 == 1\n"

def sha(data): return hashlib.sha256(data).hexdigest()
def tree(root):
  return {str(q.relative_to(root)):q.read_bytes() for q in root.rglob("*") if q.is_file()}
def canonical(data): return json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()

def record_oracle(node):
  return {"nodeid":PATH+"::"+node,"phase":"call","outcome":"failed",
          "exception":BRANCH,"assertion":None,"partial":False}

def v2_signature(node):
  return sha(canonical({"receipt_version":2,"diagnostic_version":1,"availability":"available",
    "completeness":"complete","diagnostic_partial":False,"diagnostic_error":None,
    "returncode":1,"selectors":[PATH],"records":[record_oracle(node)]}))

class Fixture:
  def __init__(self,root,structured,kind="branch"):
    self.root,self.structured,self.kind=root,structured,kind
    self.head=p.git(root,"rev-parse","HEAD")
    self.refs=p.ref_snapshot(root)
    self.taskfile=root/".local/quota-branch-task.json"
    self.original="original-branch"
    self.prefix="resumed-branch"
    self.prior_dir=root/".local/two-site-refactor/pipeline"/self.original
    self.prior_dir.mkdir(parents=True)
    self.ev=root/".local/two-site-refactor/pipeline"/self.prefix
    self.task={"task_id":"QUOTA-BRANCH-FIXTURE","goal":"Native same-lineage recurrence",
      "context_files":[PATH],"allowed_files":[PATH],"requirements":["R1"],"test_selectors":[PATH]}
    r.write_json(self.taskfile,self.task)
    _,self.sources=r.load_task(root,self.taskfile)
    self.initial=BRANCH_SOURCE if kind!="raw" else RAW_ONLY
    self.current=BRANCH_SOURCE.replace("test_a","test_b") if kind=="branch" else OTHER if kind=="different" else BRANCH_SOURCE.replace("test_a","test_b")
    self.calls,self.created=[],[]
    self.w0=self.record("worker",self.original+"-worker-0",self.initial)
    self.w0sha=r.file_sha256(self.w0/"result.json")
    result=r.unique_json((self.w0/"result.json").read_text())
    self.prior_receipt=receipt_mod.run_candidate_tests(root,self.task,result,self.w0sha,
      timeout=60,structured_diagnostics=structured)
    self.receiptfile=self.prior_dir/"pre-review-tests-0.json"
    r.write_json(self.receiptfile,self.prior_receipt)
    self.raw_receipt=self.receiptfile.read_bytes()
    self.feedbackfile=self.prior_dir/"feedback-correction-1.json"
    self.feedback=p.test_gate_feedback(self.prior_receipt,self.w0sha)
    r.write_json(self.feedbackfile,self.feedback)
    self.w1=self.record("worker",self.prefix+"-worker-1",self.current,
                        proposal=self.w0/"result.json",feedback=self.feedbackfile)
    self.w1sha=r.file_sha256(self.w1/"result.json")
    self.fingerprint="e"*64
    self.artifacts={"worker_index":1,"correction_origin":"TEST_FAILED",
      "original_run_prefix":self.original,"final_worker_run_id":self.original+"-worker-0",
      "final_reviewer_run_id":None,"proposal_path":(self.w0/"result.json").relative_to(root).as_posix(),
      "reviewer_path":None,"proposal_sha256":self.w0sha,
      "pre_review_test_path":self.receiptfile.relative_to(root).as_posix(),
      "pre_review_test_sha256":sha(self.raw_receipt),"pre_review_test_state":"FAILED",
      "expected_feedback_path":self.feedbackfile.relative_to(root).as_posix(),
      "source_head":self.head,"task_id":self.task["task_id"],
      "semantic_task_fingerprint":self.fingerprint}
    self.request={"role":"worker","original_request_id":"original-quota-fixture",
      "run_prefix":self.prefix,"timeout":60,"task_id":self.task["task_id"],
      "source_head":self.head,"semantic_task_fingerprint":self.fingerprint}
    self.terminal={"request_id":"original-quota-fixture","fixture":"quota plumbing only"}
    self.returned={"state":"WORKER_PROPOSAL_READY","task_id":self.task["task_id"],
      "source_head":self.head,"final_worker_run_id":self.prefix+"-worker-1",
      "proposal_sha256":self.w1sha,"changed_paths":[PATH],
      "proposal_path":(self.w1/"result.json").relative_to(root).as_posix(),
      "correction_count":1,"semantic_task_fingerprint":self.fingerprint}
    (root/".local/reservation.json").write_bytes(b'{"state":"RESERVED","sequence":1}\n')
    (root/".local/cost.json").write_bytes(b'{"observed_tokens":17,"unknown":false}\n')
    self.before=tree(root/".local")
    self.inputs=copy.deepcopy((self.artifacts,self.request,self.terminal,self.returned,self.task,self.sources))
    self.expected_calls=[
      ((root,self.taskfile,"worker",self.prefix+"-worker-2",60),
       {"feedback":self.ev/"feedback-correction-2.json","proposal":self.w1/"result.json","cli":None}),
      ((root,self.taskfile,"verifier",self.prefix+"-reviewer-2",60),
       {"feedback":None,"proposal":root/".local/two-site-refactor/proposals"/(self.prefix+"-worker-2")/"result.json",
        "cli":None,"test_evidence":self.ev/"pre-review-tests-2.json"})]

  def record(self,role,run_id,content=None,proposal=None,feedback=None,test_evidence=None):
    out=self.root/".local/two-site-refactor/proposals"/run_id
    out.mkdir(parents=True)
    result={"status":"PROPOSAL_READY" if role=="worker" else "VERIFIED",
      "summary":"Local tool-free Provider fixture","covered_requirements":[1],"findings":[],
      "changes":[{"path":PATH,"original_sha256":self.sources[PATH]["sha256"],"content":content}] if role=="worker" else []}
    result=r.validate_result(result,self.task,self.sources,role,enforce_raw_transport=False)
    r.write_json(out/"result.json",result)
    status={"run_id":run_id,"role":role,"head":self.head,"model":p.expected_role_model(role,self.task["task_id"]),
      "state":result["status"],"conversation_id":"fixture-"+run_id,"result_sha256":r.file_sha256(out/"result.json")}
    material={"task":self.task,"sources":self.sources,"source_head":self.head}
    if proposal is not None:
      prior=r.unique_json(proposal.with_name("status.json").read_text())
      material["proposal"]=proposal.read_text()
      status.update(input_worker_run_id=prior["run_id"],input_worker_conversation_id=prior["conversation_id"],
                    input_proposal_sha256=r.file_sha256(proposal))
    if feedback is not None: status["input_feedback_sha256"]=r.file_sha256(feedback)
    if test_evidence is not None:
      raw=test_evidence.read_bytes()
      passed=receipt_mod.validate_test_receipt(r.unique_json(raw.decode()),r.file_sha256(proposal),require_passed=True)
      assert passed["state"]=="PASSED" and passed["returncode"]==0 and passed["receipt_version"]==2
      material["test_evidence"]=passed
      status["input_test_evidence_sha256"]=sha(raw)
    r.write_json(out/"task.json",material)
    r.write_json(out/"status.json",status)
    return out

  def provider(self,*args,**kwargs):
    expected=self.expected_calls[len(self.calls)]
    assert (args,kwargs)==expected
    assert kwargs["proposal"]==expected[1]["proposal"] and set(kwargs)==set(expected[1])
    self.calls.append((args,kwargs.copy()))
    role,run_id=args[2:4]
    out=self.record(role,run_id,PASS if role=="worker" else None,proposal=kwargs["proposal"],
      feedback=kwargs["feedback"],test_evidence=kwargs.get("test_evidence"))
    self.created.append(out)
    return out

  def run(self):
    # Only these three quota plumbing seams are modeled; every reader/binder is real.
    with patch.object(context_mod,"validate_quota_continuation_request",side_effect=lambda root,request:(copy.deepcopy(self.request),self.taskfile,None)),\
         patch.object(post_mod,"validate_quota_continuation_request",side_effect=lambda root,request:(copy.deepcopy(self.request),self.taskfile,None)),\
         patch.object(context_mod,"read_terminal_quota_record",side_effect=lambda *a:copy.deepcopy(self.terminal)),\
         patch.object(post_mod,"read_terminal_quota_record",side_effect=lambda *a:copy.deepcopy(self.terminal)),\
         patch.object(context_mod,"derive_worker_resume_artifacts",side_effect=lambda *a:copy.deepcopy(self.artifacts)),\
         patch.object(post_mod,"derive_worker_resume_artifacts",side_effect=lambda *a:copy.deepcopy(self.artifacts)),\
         patch.object(p,"runner_run",self.provider):
      return one.execute_worker_one_post_result_remainder(self.root,self.request,self.returned)

  def custody(self):
    assert (self.artifacts,self.request,self.terminal,self.returned,self.task,self.sources)==self.inputs
    assert {name:(self.root/".local"/name).read_bytes() for name in self.before}==self.before
    assert self.receiptfile.read_bytes()==self.raw_receipt
    assert self.feedbackfile.read_bytes()==r.serialize_json(self.feedback)

  def prior_oracles(self):
    assert self.prior_receipt["state"]=="FAILED" and self.prior_receipt["returncode"]==1
    assert self.prior_receipt["proposal_sha256"]==self.w0sha and self.prior_receipt["selectors"]==[PATH]
    assert sha(self.raw_receipt)==self.artifacts["pre_review_test_sha256"]
    evidence=load_worker_correction_evidence(self.root,self.taskfile,self.artifacts)
    assert evidence["receipt"]==self.prior_receipt and evidence["feedback"]==self.feedback
    assert evidence["worker_info"]["result_sha"]==self.w0sha
    if self.kind!="raw":
      assert p._branch_root_failure_factor(evidence["receipt"])==FACTOR
      if self.structured:
        assert evidence["receipt"]["pytest_diagnostics"]=={"version":1,"partial":False,"records":[record_oracle("test_a")]}
        assert p.test_failure_signature(evidence["receipt"])==v2_signature("test_a")

@pytest.fixture
def make(full):
  return lambda structured,kind="branch":Fixture(full,structured,kind)

@pytest.mark.parametrize("structured",[False,True])
def test_changed_node_same_branch_stops(make,structured):
  f=make(structured)
  f.prior_oracles()
  with pytest.raises(p.ProposalError,match="repeated branch root factor: "+FACTOR):f.run()
  assert f.calls==[] and f.created==[]
  current=r.unique_json((f.ev/"pre-review-tests-1.json").read_text())
  assert current["pytest_diagnostics"]=={"version":1,"partial":False,"records":[record_oracle("test_b")]}
  assert p.test_failure_signature(current)==v2_signature("test_b")
  assert p.test_failure_signature(current)!=p.test_failure_signature(f.prior_receipt)
  assert p._branch_root_failure_factor(current)==FACTOR
  assert not (f.ev/"feedback-correction-2.json").exists()
  assert not (f.root/".local/two-site-refactor/proposals"/(f.prefix+"-worker-2")).exists()
  assert not (f.root/".local/two-site-refactor/proposals"/(f.prefix+"-reviewer-1")).exists()
  f.custody()

@pytest.mark.parametrize("structured",[False,True])
def test_different_cause_reaches_native_pass_and_reviewer(make,structured):
  f=make(structured,"different")
  f.prior_oracles()
  result=f.run()
  assert f.calls==f.expected_calls and result["state"]=="READY_FOR_CODEX" and result["correction_count"]==2
  assert result["final_worker_run_id"]==f.prefix+"-worker-2"
  assert result["final_reviewer_run_id"]==f.prefix+"-reviewer-2"
  current=r.unique_json((f.ev/"pre-review-tests-1.json").read_text())
  assert current["returncode"]==1 and p._branch_root_failure_factor(current) is None
  raw=(f.ev/"pre-review-tests-2.json").read_bytes()
  passed=receipt_mod.validate_test_receipt(r.unique_json(raw.decode()),
    r.file_sha256(f.created[0]/"result.json"),require_passed=True)
  assert passed["returncode"]==0 and passed["pytest_diagnostics"]=={"version":1,"partial":False,"records":[]}
  assert result["pre_review_test_sha256"]==sha(raw)
  status=r.unique_json((f.created[1]/"status.json").read_text())
  assert status["input_test_evidence_sha256"]==sha(raw)
  f.custody()

def test_v2_console_branch_text_is_not_prior_history(make):
  f=make(True,"raw")
  assert BRANCH in f.prior_receipt["stdout_tail"]
  assert p._branch_root_failure_factor(f.prior_receipt) is None
  result=f.run()
  assert result["state"]=="READY_FOR_CODEX" and f.calls==f.expected_calls
  f.custody()

@pytest.mark.parametrize("fault",["missing","malformed","sha","feedback"])
def test_prior_custody_faults_before_dispatch(make,fault):
  f=make(True)
  f.prior_oracles()
  path=f.feedbackfile if fault=="feedback" else f.receiptfile
  old=path.read_bytes()
  old_artifacts=copy.deepcopy(f.artifacts)
  moved=path.with_name(path.name+".owned-missing")
  try:
    if fault=="missing":path.rename(moved)
    elif fault=="malformed":
      invalid=copy.deepcopy(f.prior_receipt); invalid["receipt_version"]=3
      r.write_json(path,invalid)
      f.artifacts["pre_review_test_sha256"]=r.file_sha256(path)
    elif fault=="sha":path.write_bytes(old+b" ")
    else:
      invalid=r.unique_json(old.decode()); invalid["findings"]=["different unbound cause"]
      r.write_json(path,invalid)
    with pytest.raises(p.ProposalError):f.run()
    assert f.calls==[] and f.created==[] and not f.ev.exists()
  finally:
    if fault=="missing":moved.rename(path)
    else:path.write_bytes(old)
    f.artifacts=old_artifacts
  f.custody()

@pytest.mark.parametrize("structured",[False,True])
def test_same_witness_actual_handoff_omission_then_restore(make,structured):
  f=make(structured)
  f.prior_oracles()
  actual=one.execute_worker_one_post_result_remainder
  module=ast.parse(inspect.getsource(actual))
  calls=[n for n in ast.walk(module) if isinstance(n,ast.Call)
    and isinstance(n.func,ast.Name) and n.func.id=="_run_post_worker_remainder"]
  assert len(calls)==1
  keys=[k for k in calls[0].keywords if k.arg=="seen_branch_factors"]
  assert len(keys)==1
  calls[0].keywords.remove(keys[0])
  namespace={}
  exec(compile(ast.fix_missing_locations(module),"actual-history-handoff-omission","exec"),one.__dict__,namespace)
  old=namespace[actual.__name__]
  assert inspect.signature(old)==inspect.signature(actual)
  archived=[]
  def archive(label):
    target=f.root/".local/owned-archive"/label
    target.mkdir(parents=True)
    for path in [f.ev,*f.created]:
      assert path.is_relative_to(f.root/".local") and path.exists() and not path.is_symlink()
      saved=tree(path)
      dest=target/path.name
      assert not dest.exists()
      path.rename(dest)
      assert tree(dest)==saved
      archived.append((dest,saved))
    f.calls.clear(); f.created.clear()
  def witness():
    caught=False
    try:f.run()
    except p.ProposalError as error:
      assert str(error)=="repeated branch root factor: "+FACTOR
      caught=True
    assert caught
    assert f.calls==[]  # Same oracle fails specifically at the omitted handoff's extra Worker2.
    assert not (f.ev/"feedback-correction-2.json").exists()
    f.custody()
  witness(); archive("first")
  try:
    with patch.object(one,"execute_worker_one_post_result_remainder",old),pytest.raises(AssertionError):witness()
    assert f.calls==f.expected_calls
  finally:
    assert one.execute_worker_one_post_result_remainder is actual
    archive("omitted")
  witness()
  assert one.execute_worker_one_post_result_remainder is actual
  for path,saved in archived:assert tree(path)==saved
  f.custody()
