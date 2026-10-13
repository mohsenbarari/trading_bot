"""Offline unchanged parser/Runner evidence; fake CLI is not genuine Gemini.

Only the existing local CLI boundary is simulated; primary errors are native.
"""
from __future__ import annotations
import os
os.environ["APP_ENV_FILE"]="config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()
import ast
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from scripts import two_site_proposal_pipeline as p
from scripts import two_site_proposal_runner as r
from tests import test_two_site_proposal_runner as support

MESSAGE="correction worker made no implementation progress"
EMPTY="empty or unchanged file"
PLAN="plan-only or no-op response is not implementation"
REBASE="complete no-op rejection: rebase yielded no canonical changes"
CODE="assert 1 == 1\n"
LARGE=CODE + "# retained padding\n" * 500

def sha(data):
  return hashlib.sha256(data).hexdigest()

def tree(root):
  return {str(path.relative_to(root)):path.read_bytes()
      for path in root.rglob("*") if path.is_file()}

class Case:
  """Owned fixture with predeclared input/evidence oracles."""

  def __init__(self,kind="unchanged",*,initial=False):
    self.s=support.ProposalContracts()
    self.s.setUp()
    self.root,self.task=self.s.root,self.s.task
    self.kind,self.initial=kind,initial
    self.path="source.py" if initial or kind in ("empty","rebase","partial","unforced") else "test_new.py"
    if self.path == "source.py":
      self.task["allowed_files"]=[self.path] + (["test_new.py"] if kind == "partial" else [])
      self.s.save_task()
      _,self.s.sources=r.load_task(self.root,self.s.task_path)
    self.orig={"source.py":{"content":"VALUE = 1\n","sha256":sha(b"VALUE = 1\n")}}
    if "test_new.py" in self.task["allowed_files"]:
      self.orig["test_new.py"]={"content":None,"sha256":None}
    assert self.s.sources == self.orig
    self.code=("VALUE = 1\n" if initial else "VALUE = 2\n" if self.path == "source.py" else CODE)
    if kind in ("large","unforced"):
      self.code=LARGE
    prior={"status":"PROPOSAL_READY","summary":"Original candidate",
      "covered_requirements":[1,2],"findings":[],"changes":[{
        "path":self.path,"original_sha256":self.orig[self.path]["sha256"],
        "content":self.code}]}
    if kind == "partial":
      prior["changes"].append({"path":"test_new.py","original_sha256":None,"content":CODE})
    self.prior=self.s.worker_evidence(self.root / "prior-worker",result=prior,
                          conversation_id="prior-worker-session")
    self.fb=self.root / "feedback.json"
    self.fb.write_text(json.dumps({"reviewer":"codex","reviewed_proposal_sha256":sha(self.prior.read_bytes()),
      "findings":["Repair the implementation"],"test_evidence":["Retain original evidence"]}),encoding="utf-8")
    self.cli=Path(support.__file__).resolve()
    self.file=self.s.task_path
    self.out_dir=self.root / ".local/two-site-refactor/proposals"
    (self.root / "reservation.json").write_bytes(b'{"state":"RESERVED","sequence":1}\n')
    (self.root / "cost.json").write_bytes(b'{"observed_tokens":17,"unknown":false}\n')
    self.before=tree(self.root)
    self.calls,self.order,self.errors,self.records=[],[],[],[]
    self.body=self.response(kind)
    self.raw=self.stream(self.body)
    self.inputs=deepcopy((self.task,self.orig,self.body,self.raw))

  def close(self):
    self.s.doCleanups()

  def response(self,kind):
    content,replacements=self.code,[]
    if kind == "whitespace":content=" \n\t"
    elif kind == "empty":content=""
    elif kind == "forced-empty":content=""
    elif kind == "changed":content="assert 2 == 2\n"
    elif kind in ("rebase","partial"):
      content,replacements=None,[{"old":"VALUE = 2\n","new":"VALUE = 1\n"}]
    elif kind == "unforced":content=LARGE
    result={"status":"PROPOSAL_READY","summary":"Bound correction",
      "covered_requirements":[1,2],"findings":[],"changes":[{"path":self.path,
        "original_sha256":sha(self.code.encode()) if not self.initial else self.orig[self.path]["sha256"],
        "content":content,"replacements":replacements}]}
    if kind == "plan":result["changes"]=[]
    if kind == "minimal-plan":result={"status":"PROPOSAL_READY","changes":[]}
    if kind == "wrong-sha":result["changes"][0]["original_sha256"]="0" * 64
    if kind == "malformed":del result["summary"]
    if kind == "syntax":result["changes"][0]["content"]="def invalid(:\n    pass\n"
    if kind == "unsafe":result["changes"][0]["content"]="value='\x00'\n"
    return result

  @staticmethod
  def stream(payload,*,model=r.WORKER_MODEL,session="session-one",status="SUCCESS",error=None):
    envelope={"conversation_id":session,"status":status,"response":json.dumps(payload)}
    if error is not None:envelope["error"]=error
    return "\n".join(json.dumps(event) for event in [
      {"event":"init","conversation_id":session,"init":{"model":model}},
      {"event":"result","conversation_id":session,"result":envelope}]) + "\n"

  def check(self):
    self.order.append("check")
    assert {name:(self.root / name).read_bytes() for name in self.before} == self.before

  def runner(self,*a,**kw):
    self.calls.append((a,kw.copy()))
    self.order.append("dispatch")
    assert len(a) == 5 and a[:3] == (self.root,self.file,"worker") and a[4] == 30
    assert set(kw) == {"feedback","proposal","cli"}
    assert kw["cli"] is self.cli
    assert kw["feedback"] is (None if self.initial else self.fb)
    assert kw["proposal"] is (None if self.initial else self.prior)
    out=self.out_dir / a[3]
    try:
      return self.s.invoke_fake_cli(run_id=a[3],raw_output=self.raw,
        feedback=kw["feedback"],proposal=kw["proposal"])
    except BaseException as error:
      self.errors.append(error)
      self.records.append((out,tree(out),self.raw))
      raise

  def run(self,*,state_check=None,base="correction",actor=None):
    with patch.object(p,"runner_run",self.runner):
      return (actor or p.run_provider_session)(self.root,self.file,"worker",base,30,
        state_check or self.check,feedback=None if self.initial else self.fb,
        proposal=None if self.initial else self.prior,cli=self.cli,expected_head="head")

  def custody(self,ids):
    assert [a[3] for a,_ in self.calls] == ids
    for (a,kw),run_id in zip(self.calls,ids):
      assert a == (self.root,self.file,"worker",run_id,30)
      assert kw == {"feedback":None if self.initial else self.fb,
               "proposal":None if self.initial else self.prior,"cli":self.cli}
    assert (self.task,self.orig,self.body,self.raw) == self.inputs
    assert {name:(self.root / name).read_bytes() for name in self.before} == self.before
    for out,saved,raw in self.records:
      assert tree(out) == saved
      assert {"status.json","task.json","prompt.txt","stream.jsonl","stderr.txt"} <=set(saved)
      assert not {"result.json","delta-result.json","proposal.patch"} & set(saved)
      status=json.loads(saved["status.json"])
      assert status["state"] == "BLOCKED" and status["authority"] == "PROPOSAL_ONLY_NOT_ACCEPTANCE"
      assert saved["stream.jsonl"] == raw.encode()
      bound=json.loads(saved["task.json"])
      assert bound["task"] == self.task and bound["sources"] == self.orig
      if not self.initial:assert bound["proposal"] == self.prior.read_text()

@pytest.fixture
def make():
  owned=[]
  def create(*a,**kw):
    value=Case.__new__(Case)
    owned.append(value)
    value.__init__(*a,**kw)
    return value
  try:yield create
  finally:
    for value in reversed(owned):value.close()

def nonprogress(f,cause,text):
  with pytest.raises(r.ProposalError) as got:f.run()
  f.custody(["correction"])
  error=got.value
  assert f.order == ["check","dispatch","check"]
  assert type(error) is r.ProposalError and str(error) == MESSAGE
  assert error.__cause__ is f.errors[0] and type(error.__cause__) is r.RetryableProviderOutputError
  assert type(error.__cause__.__cause__) is cause and str(error.__cause__.__cause__) == text
  assert not (f.out_dir / "correction-retry-1").exists()
  assert not (f.root / ".local/two-site-refactor/pipeline").exists()

@pytest.mark.parametrize("kind,cause,text",[
  ("unchanged",r.ProposalError,EMPTY),("large",r.ProposalError,EMPTY),
  ("whitespace",r.ProposalError,EMPTY),("empty",r.ProposalError,EMPTY),
  ("plan",r.ProposalError,PLAN),("minimal-plan",r.ProposalError,PLAN),
  ("rebase",r.CorrectionDeltaError,REBASE)])
def test_native_nonprogress(make,kind,cause,text):
  f=make(kind)
  if kind == "large":assert len(f.code.encode()) >=8192
  nonprogress(f,cause,text)
  if kind == "minimal-plan":
    assert set(f.body) == {"status","changes"}  # Cause precedes complete schema.

def test_state_drift_precedence(make):
  f=make("rebase")
  drift=RuntimeError("state changed")
  def check():
    f.check()
    if len(f.calls) == 1:raise drift
  with pytest.raises(RuntimeError) as got:
    f.run(state_check=check)
  assert got.value is drift and f.order == ["check","dispatch","check"]
  assert type(f.errors[0].__cause__) is r.CorrectionDeltaError
  f.custody(["correction"])

@pytest.mark.parametrize("kind",["malformed","forced-empty","unforced","syntax"])
def test_other_output_errors(make,kind):
  f=make(kind)
  with pytest.raises(r.ProposalError,match="after 2 output attempts") as got:
    f.run()
  assert type(got.value) is r.ProposalError and got.value.__cause__ is f.errors[-1]
  assert f.order == ["check","dispatch","check","dispatch"]
  f.custody(["correction","correction-retry-1"])
  assert all(type(error) is r.RetryableProviderOutputError for error in f.errors)
  assert all(str(error.__cause__) not in {EMPTY,PLAN,REBASE} for error in f.errors)

@pytest.mark.parametrize("kind,text",[("unchanged",EMPTY),("minimal-plan",PLAN)])
def test_initial_nonprogress(make,kind,text):
  f=make(kind,initial=True)
  with pytest.raises(r.ProposalError,match="after 2 output attempts"):
    f.run(base="initial")
  assert [str(error.__cause__) for error in f.errors] == [text,text]
  assert f.order == ["check","dispatch","check","dispatch"]
  f.custody(["initial","initial-retry-1"])

@pytest.mark.parametrize("kind,changes",[
  ("changed",[{"path":"test_new.py","original_sha256":None,"content":"assert 2 == 2\n"}]),
  ("partial",[{"path":"test_new.py","original_sha256":None,"content":CODE}])])
def test_changed_partial_success(make,kind,changes):
  f=make(kind)
  name,out=f.run()
  assert name == "correction" and out == f.out_dir / "correction"
  assert f.errors == [] and f.order == ["check","dispatch"]
  assert json.loads((out / "result.json").read_text())["changes"] == changes
  f.custody(["correction"])

@pytest.mark.parametrize("kind",["wrong-sha","unsafe"])
def test_native_hard_errors(make,kind):
  f=make(kind)
  with pytest.raises(r.ProposalError) as got:
    f.run()
  assert type(got.value) is r.ProposalError and str(got.value) !=MESSAGE
  assert f.order == ["check","dispatch","check"]
  f.custody(["correction"])

def test_native_session_errors(make):
  for name,status,error in [("auth","ERROR","authentication failed"),("quota","ERROR","quota limit"),
                ("truncation","ERROR","MODEL_OUTPUT_TRUNCATED")]:
    f=make()
    f.raw=f.stream(f.body,status=status,error=error)
    f.inputs=deepcopy((f.task,f.orig,f.body,f.raw))
    with pytest.raises(r.ProposalError) as got:
      f.run(base=name)
    assert str(got.value) !=MESSAGE and type(got.value) is r.ProposalError
    assert f.order == ["check","dispatch","check"]
    f.custody([name])
  for name,raw in [("model",Case.stream({},model="wrong-model")),
           ("session",Case.stream({}).replace('"conversation_id": "session-one"','"conversation_id": "other"',1))]:
    f=make(); f.raw=raw; f.inputs=deepcopy((f.task,f.orig,f.body,f.raw))
    with pytest.raises(r.ProposalError) as got:f.run(base=name)
    assert str(got.value) !=MESSAGE and type(got.value) is r.ProposalError
    f.custody([name])
  t=make()
  t.raw=t.stream({},status="SUCCESS")
  # Genuine missing-response session error.
  events=[json.loads(line) for line in t.raw.splitlines()]
  del events[1]["result"]["response"]
  t.raw="\n".join(json.dumps(event) for event in events) + "\n"
  t.inputs=deepcopy((t.task,t.orig,t.body,t.raw))
  with pytest.raises(r.ProposalError,match="after 3 attempts"):
    t.run()
  assert t.order == ["check","dispatch"] * 3
  t.custody(["correction","correction-retry-1","correction-retry-2"])

def test_mixed_session_then_nonprogress(make):
  f=make("rebase")
  events=[json.loads(line) for line in f.raw.splitlines()]
  del events[1]["result"]["response"]
  raws=("\n".join(json.dumps(e) for e in events)+"\n",f.raw)
  actual=f.runner
  def dispatch(*a,**kw):
    raw=f.raw
    try:
      f.raw=raws[len(f.calls)]
      return actual(*a,**kw)
    finally:f.raw=raw
  with patch.object(f,"runner",dispatch),pytest.raises(r.ProposalError) as got:f.run()
  f.custody(["correction","correction-retry-1"])
  assert f.order==["check","dispatch","check","dispatch","check"]
  assert str(got.value)==MESSAGE and got.value.__cause__ is f.errors[1]
  assert type(f.errors[0]) is r.RetryableProviderSessionError
  assert type(f.errors[1].__cause__) is r.CorrectionDeltaError

def test_verifier_incomplete_inputs(make):
  f=make()
  verified={"status":"VERIFIED","summary":"Native review","covered_requirements":[1,2],"findings":[],"changes":[]}
  raw=f.stream(verified,model=r.MODEL)
  result,_=r._parse_stream(raw,f.task,f.orig,"verifier",expected_model=r.MODEL)
  assert result == verified
  calls,checks=[],[]
  def verifier(*a,**kw):
    calls.append((a,kw.copy()))
    assert a == (f.root,f.file,"verifier","review",30)
    assert kw == {"feedback":None,"proposal":f.prior,"cli":f.cli}
    r._parse_stream(raw,f.task,f.orig,"verifier",expected_model=r.MODEL)
    return f.root / "local-review"  # Parser-only seam.
  with patch.object(p,"runner_run",verifier):
    actual=p.run_provider_session(f.root,f.file,"verifier","review",30,lambda:checks.append(True),
      proposal=f.prior,cli=f.cli,expected_head="head")
  assert actual == ("review",f.root / "local-review") and len(calls) == 1 and checks == [True]
  # Actual Runner rejects incomplete inputs before parsing.
  for i,(feedback,proposal) in enumerate([(f.fb,None),(None,f.prior)]):
    calls.clear(); checks.clear()
    def incomplete(*a,**kw):
      calls.append((a,kw.copy()))
      return f.s.invoke_fake_cli(run_id=a[3],feedback=kw["feedback"],proposal=kw["proposal"])
    with patch.object(p,"runner_run",incomplete),pytest.raises(r.ProposalError) as got:
      p.run_provider_session(f.root,f.file,"worker","partial-" + str(i),30,lambda:checks.append(True),
        feedback=feedback,proposal=proposal,cli=f.cli,expected_head="head")
    assert str(got.value) == "correction worker requires both proposal and feedback"
    assert len(calls) == 1 and checks == [True,True]

def test_same_native_policy_omission_restore(make):
  f=make("rebase")
  actual=p.run_provider_session
  module=ast.parse(inspect.getsource(actual))
  function=module.body[0]
  handler=next(node for node in ast.walk(function) if isinstance(node,ast.ExceptHandler)
          and isinstance(node.type,ast.Name) and node.type.id == "RetryableProviderOutputError")
  policies=[node for node in handler.body if isinstance(node,ast.If)
        and any(isinstance(child,ast.Constant) and child.value == MESSAGE for child in ast.walk(node))]
  assert len(policies) == 1
  handler.body.remove(policies[0])
  namespace={}
  exec(compile(ast.fix_missing_locations(module),"actual-policy-omission","exec"),p.__dict__,namespace)
  old=namespace["run_provider_session"]
  assert inspect.signature(old) == inspect.signature(actual)
  archived=[]
  def archive(label):
    target=f.root / "archive" / label; target.mkdir(parents=True)
    for out,saved,_ in f.records:
      destination=target / out.name
      assert not destination.exists()
      out.rename(destination)
      assert tree(destination) == saved
      archived.append((destination,saved))
    f.calls.clear(); f.order.clear(); f.errors.clear(); f.records.clear()
  def witness():nonprogress(f,r.CorrectionDeltaError,REBASE)
  witness()
  archive("first")
  try:
    with patch.object(p,"run_provider_session",old):
      with pytest.raises(AssertionError):witness()
      assert [a[3] for a,_ in f.calls] == ["correction","correction-retry-1"]
      assert all(type(error.__cause__) is r.CorrectionDeltaError for error in f.errors)
  finally:
    assert p.run_provider_session is actual
    archive("old")
  witness()
  assert p.run_provider_session is actual
  for directory,saved in archived:assert tree(directory) == saved
  f.custody(["correction"])

def test_defensive_lookalikes(make):
  # Defensive controls, not primary native causes.
  class Derived(r.ProposalError):pass
  class DeltaDerived(r.CorrectionDeltaError):pass
  for cause in [r.ProposalError("prefix " + EMPTY),r.ProposalError(EMPTY + " suffix"),
         Derived(EMPTY),r.CorrectionDeltaError("prefix " + REBASE),
         r.CorrectionDeltaError(REBASE + " suffix"),DeltaDerived(REBASE),None]:
    f=make(); calls,checks=[],[]
    wrapper=r.RetryableProviderOutputError("defensive obtained-worker-error")
    wrapper.__cause__=cause
    if cause is None:wrapper.__context__=r.ProposalError(EMPTY)
    def boundary(*a,**kw):
      calls.append((a,kw.copy()))
      assert kw == {"feedback":f.fb,"proposal":f.prior,"cli":f.cli}
      raise wrapper
    with patch.object(p,"runner_run",boundary),pytest.raises(r.ProposalError,match="after 2 output attempts"):
      p.run_provider_session(f.root,f.file,"worker","defensive",30,lambda:checks.append(True),
        feedback=f.fb,proposal=f.prior,cli=f.cli,expected_head="head")
    assert calls == [((f.root,f.file,"worker",name,30),
      {"feedback":f.fb,"proposal":f.prior,"cli":f.cli})
      for name in ["defensive","defensive-retry-1"]]
    assert checks == [True,True]
