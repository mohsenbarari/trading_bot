"""Temporary actual Git plus real pipeline validators; synthetic sessions are lab only."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from scripts import two_site_main_maintenance_evidence as subject
from scripts.two_site_main_maintenance_record import build_main_maintenance_record, serialize_main_maintenance_record
from scripts.two_site_proposal_runner import serialize_json, unique_json
from scripts.two_site_proposal_pipeline import expected_role_model, validate_run, load_verifier_pre_review_receipt
from scripts.two_site_protected_main_approval_record import parse_protected_main_approval_record
from scripts.two_site_main_maintenance_git import verify_main_maintenance_git_chain

ERROR = "invalid main maintenance evidence"
CONTROL = "scripts/two_site_control.py"
WORK = "scripts/two_site_worker.py"
NOTE = "docs/note.md"
REUSED = "core/from_main.py"
MAIN, ORIGIN, CANDIDATE = "refs/heads/main", "refs/remotes/origin/main", "refs/heads/candidate/wa-ir-standby-v1"
BRANCH = "refactor/two-site-architecture"


def git(root, *args):
    return subprocess.check_output(["/usr/bin/git","--no-replace-objects","-C",str(root),*args],
        env={"PATH":"/usr/bin:/bin","LANG":"C.UTF-8","GIT_CONFIG_NOSYSTEM":"1",
             "GIT_TERMINAL_PROMPT":"0","GIT_AUTHOR_NAME":"AE lab","GIT_AUTHOR_EMAIL":"ae@example.invalid",
             "GIT_COMMITTER_NAME":"AE lab","GIT_COMMITTER_EMAIL":"ae@example.invalid"},
        stderr=subprocess.DEVNULL,timeout=10).decode().strip()


def save(root, path, content):
    target = root/path
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(content,encoding="utf-8")


def commit(root, path, content):
    save(root,path,content)
    git(root,"add","--",path)
    git(root,"commit","-m","lab fixture")
    return git(root,"rev-parse","HEAD")


def artifact(root, path, value):
    target = root/path
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(serialize_json(value))
    target.chmod(0o600)
    return hashlib.sha256(serialize_json(value)).hexdigest()


def read(root,path):
    return unique_json((root/path).read_bytes().decode())


def evidence(root, prefix, parent, path, old, new, corrections=0):
    assignment = "LAB_"+prefix.replace("-", "_")
    controller = "maint-20261004T000000Z-"+hashlib.sha256(prefix.encode()).hexdigest()[:16]
    task = {"task_id":prefix,"goal":"Lab synthetic metadata, never real Provider evidence "+assignment,
        "context_files":[],"allowed_files":[path],"requirements":["one independent laboratory requirement"],
        "test_selectors":["tests/test_fixture.py"]}
    sources = {path:{"content":old,"sha256":hashlib.sha256(old.encode()).hexdigest() if old is not None else None}}
    task_path = ".local/two-site-refactor/tasks/"+prefix+".json"
    task_sha = artifact(root, task_path, task)
    artifact(root, ".local/two-site-refactor/provider-broker/maintenance-runs/"+controller+".json",
        {"schema_version":"1.0", "request":{"schema_version":"1.0", "request_id":"1"*32,
            "controller_run_id":controller, "run_prefix":prefix, "source_head":parent,
            "task_path":task_path, "task_sha256":task_sha, "assignment_id":assignment,
            "assignment_sha256":"2"*64, "created_at":1791072000, "timeout":900}})
    previous = None
    for index in range(corrections+1):
        worker = prefix+f"-worker-{index}"
        content = new if index == corrections else new+f"# rejected {index}\n"
        result = {"status":"PROPOSAL_READY","summary":"explicit laboratory Worker fixture",
            "covered_requirements":[1],"findings":[],"changes":[{"path":path,
                "original_sha256":sources[path]["sha256"],"content":content}]}
        proposal_raw = serialize_json(result)
        proposal_sha = hashlib.sha256(proposal_raw).hexdigest()
        status = {"run_id":worker,"role":"worker","state":"PROPOSAL_READY","head":parent,
            "controller_run_id":controller,
            "model":expected_role_model("worker",prefix),"result_sha256":proposal_sha,"conversation_id":worker+"-session"}
        snapshot = {"task":copy.deepcopy(task),"sources":copy.deepcopy(sources),"source_head":parent}
        if previous is not None:
            previous_id, previous_sha, previous_raw = previous
            feedback = {"reviewer":"test-gate","reviewed_proposal_sha256":previous_sha,
                "findings":["laboratory rejection only"],"test_evidence":["fixture correction"]}
            feedback_sha = artifact(root,f".local/two-site-refactor/pipeline/{prefix}/feedback-correction-{index}.json",feedback)
            status.update(input_worker_run_id=previous_id,input_proposal_sha256=previous_sha,
                input_worker_conversation_id=previous_id+"-session",input_feedback_sha256=feedback_sha)
            snapshot.update(proposal=previous_raw.decode(),feedback=feedback)
        base = ".local/two-site-refactor/proposals/"+worker+"/"
        artifact(root,base+"result.json",result)
        artifact(root,base+"status.json",status)
        artifact(root,base+"task.json",snapshot)
        previous = (worker,proposal_sha,proposal_raw)
    receipt = {"state":"PASSED","proposal_sha256":proposal_sha,"selectors":task["test_selectors"],
        "unsupported_specs":[],"returncode":0,"stdout_tail":"Laboratory receipt, not a real pytest run",
        "stderr_tail":"","snapshot_files":1,"snapshot_bytes":10,"isolation":"laboratory fixture"}
    test_sha = artifact(root,f".local/two-site-refactor/pipeline/{prefix}/pre-review-tests-{corrections}.json",receipt)
    reviewer = prefix+f"-reviewer-{corrections}"
    reviewed = {"status":"VERIFIED","summary":"explicit laboratory Reviewer fixture",
        "covered_requirements":[1],"findings":[],"changes":[]}
    reviewed_sha = hashlib.sha256(serialize_json(reviewed)).hexdigest()
    review_status = {"run_id":reviewer,"role":"verifier","state":"VERIFIED","head":parent,
        "controller_run_id":controller,
        "model":expected_role_model("verifier",prefix),"result_sha256":reviewed_sha,
        "conversation_id":reviewer+"-session","input_worker_run_id":worker,
        "input_proposal_sha256":proposal_sha,"input_worker_conversation_id":worker+"-session",
        "input_test_evidence_sha256":test_sha}
    review_snapshot = {"task":task,"sources":sources,"source_head":parent,"proposal":proposal_raw.decode(),"test_evidence":receipt}
    base = ".local/two-site-refactor/proposals/"+reviewer+"/"
    artifact(root,base+"result.json",reviewed)
    artifact(root,base+"status.json",review_status)
    artifact(root,base+"task.json",review_snapshot)
    return {"worker_run_id":worker,"reviewer_run_id":reviewer,"proposal_sha256":proposal_sha,"test_evidence_sha256":test_sha}


def fixture(tmp_path, corrections=0, unreviewed_main=False, unreviewed_mode=False):
    root=(tmp_path/"repo").resolve()
    root.mkdir(mode=0o700)
    git(root,"init","-b","main")
    for path,content in ((".gitignore",".local/\n"),(CONTROL,"# control\n"),(WORK,"# old\n"),(NOTE,"old note\n")):
        save(root,path,content)
        git(root,"add","--",path)
    git(root,"commit","-m","prior lab")
    old=git(root,"rev-parse","HEAD")
    git(root,"update-ref",ORIGIN,old)
    git(root,"update-ref",CANDIDATE,old)
    approved=commit(root,REUSED,"# approved main body\n")
    git(root,"switch","-c",BRANCH,old)
    pre=evidence(root,"lab-pre",old,WORK,"# old\n","# pre\n",corrections)
    if unreviewed_mode:
        (root/WORK).chmod(0o755)
    pre_head=commit(root,WORK,"# pre\n")
    pre.update(head=pre_head,parent=old,paths=[WORK])
    main=evidence(root,"lab-main",pre_head,NOTE,"old note\n","resolved note\n")
    git(root,"merge","--no-ff","--no-commit",approved)
    save(root,NOTE,"resolved note\n")
    git(root,"add","--",NOTE)
    if unreviewed_main:
        save(root,REUSED,"# unapproved hidden rewrite\n")
        git(root,"add","--",REUSED)
    git(root,"commit","-m","lab reviewed integration")
    main_head=git(root,"rev-parse","HEAD")
    main.update(head=main_head,parents=[pre_head,approved],paths=[REUSED,NOTE])
    post=evidence(root,"lab-post",main_head,WORK,"# pre\n","# post\n")
    final=commit(root,WORK,"# post\n")
    post.update(head=final,parent=main_head,paths=[WORK])
    control=git(root,"rev-parse",old+":"+CONTROL)
    def repository(head,assignment,main_ref):
        return {"head":head,"status":"","protected_refs":{MAIN:main_ref,ORIGIN:old,CANDIDATE:old},
            "control_blobs":{CONTROL:control},"assignment_scope":{"assignment_id":assignment,"stage_id":"P1-00b",
                "sha256":"7"*64,"expires_at":"2026-10-30T23:59:00Z","allowed_paths":[WORK],"forbidden_paths":[]}}
    prior=repository(old,"prior",old)
    new=repository(final,"new",approved)
    decision={"schema_version":"1.0","kind":"OWNER_APPROVED_PROTECTED_MAIN_CHANGE","reference":MAIN,
        "prior_main_sha":old,"approved_main_sha":approved,"prior_checkpoint_sha256":"d"*64,
        "prior_head":old,"decision_id":"lab-not-owner-authorization"}
    # Actual canonical owner wire parser; laboratory value grants no real approval.
    owner_wire=(__import__("json").dumps(decision,sort_keys=True,separators=(",",":"))+"\n").encode()
    owner=parse_protected_main_approval_record(owner_wire,prior_main_sha=old,approved_main_sha=approved,
        prior_checkpoint_sha256="d"*64,prior_head=old)
    bindings={"prior_checkpoint_sha256":"d"*64,"prior_repository":prior,"owner_approval":owner}
    record=build_main_maintenance_record(**bindings,new_repository=new,pre_main_commits=[pre],main_integration=main,post_main_commits=[post])
    return root,record,bindings


def good(root,record,bindings):
    frozen=copy.deepcopy([record,bindings])
    subject.verify_main_maintenance_evidence_locked(root,record,**bindings)
    assert [record,bindings]==frozen


def bad(root,record,bindings):
    frozen=copy.deepcopy([record,bindings])
    with pytest.raises(subject.MainMaintenanceEvidenceError) as caught:
        subject.verify_main_maintenance_evidence_locked(root,record,**bindings)
    assert str(caught.value)==ERROR
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert [record,bindings]==frozen


@pytest.mark.parametrize("corrections",[0,1,2])
def test_real_git_and_actual_complete_pipeline_validators(tmp_path,corrections):
    root,record,bindings=fixture(tmp_path,corrections)
    before=(git(root,"show-ref"),git(root,"status","--porcelain=v1","--untracked-files=all"))
    with (patch.object(subject,"validate_run",wraps=validate_run) as validate,
          patch.object(subject,"load_verifier_pre_review_receipt",wraps=load_verifier_pre_review_receipt) as receipt,
          patch.object(subject,"verify_main_maintenance_git_chain",wraps=verify_main_maintenance_git_chain) as actual_git):
        good(root,record,bindings)
        assert validate.call_count==6+corrections
        assert receipt.call_count==3
        assert actual_git.call_count==2
        assert actual_git.call_args_list[0]==actual_git.call_args_list[1]
    good(root,serialize_main_maintenance_record(record,**bindings),bindings)
    assert before==(git(root,"show-ref"),git(root,"status","--porcelain=v1","--untracked-files=all"))


def test_main_reuse_is_exact_owner_approved_blob_not_unreviewed_extra(tmp_path):
    root,record,bindings=fixture(tmp_path,unreviewed_main=True)
    verify_main_maintenance_git_chain(root,record,**bindings)  # all graph metadata genuinely valid
    bad(root,record,bindings)


@pytest.mark.parametrize("fault", ["missing", "task_bytes", "task_sha", "parent", "prefix", "assignment", "review_controller", "prior_controller"])
def test_actual_dispatch_assignment_task_and_all_actor_controller_bindings(tmp_path, fault):
    root,record,bindings=fixture(tmp_path,corrections=1)
    entry=record["pre_main_commits"][0]
    wb=f".local/two-site-refactor/proposals/{entry['worker_run_id']}/"
    controller=read(root,wb+"status.json")["controller_run_id"]
    path=f".local/two-site-refactor/provider-broker/maintenance-runs/{controller}.json"
    record_payload=read(root,path)
    request=record_payload["request"]
    if fault=="missing":
        (root/path).unlink()
    elif fault=="task_bytes":
        task=read(root,request["task_path"]);task["goal"]+=" changed after dispatch"
        artifact(root,request["task_path"],task)
    elif fault in ("task_sha", "parent", "prefix", "assignment"):
        key={"task_sha":"task_sha256","parent":"source_head","prefix":"run_prefix","assignment":"assignment_id"}[fault]
        request[key]={"task_sha":"0"*64,"parent":"0"*40,"prefix":"other-prefix","assignment":"OTHER_ASSIGNMENT"}[fault]
        artifact(root,path,record_payload)
    else:
        actor=entry["reviewer_run_id"] if fault=="review_controller" else "lab-pre-worker-0"
        status_path=f".local/two-site-refactor/proposals/{actor}/status.json"
        status=read(root,status_path);status["controller_run_id"]="maint-20261004T000000Z-"+"0"*16
        artifact(root,status_path,status)
    bad(root,record,bindings)


@pytest.mark.parametrize("phase",["pre","main","post"])
@pytest.mark.parametrize("file,field,value",[("status.json","conversation_id","same-session"),
    ("status.json","head","0"*40),("status.json","state","FAILED"),
    ("status.json","result_sha256","0"*64),("task.json","source_head","0"*40)])
def test_good_fault_good_every_phase_metadata(tmp_path,phase,file,field,value):
    root,record,bindings=fixture(tmp_path)
    entry={"pre":record["pre_main_commits"][0],"main":record["main_integration"],"post":record["post_main_commits"][0]}[phase]
    actor=entry["worker_run_id"]
    path=f".local/two-site-refactor/proposals/{actor}/{file}"
    original=(root/path).read_bytes()
    payload=read(root,path)
    if field=="conversation_id":
        other=f".local/two-site-refactor/proposals/{entry['reviewer_run_id']}/status.json"
        value=read(root,other)["conversation_id"]
    good(root,record,bindings)
    payload[field]=value
    artifact(root,path,payload)
    bad(root,record,bindings)
    (root/path).write_bytes(original)
    good(root,record,bindings)


def test_hash_shaped_source_not_actual_parent_blob(tmp_path):
    root,record,bindings=fixture(tmp_path)
    prefix=".local/two-site-refactor/proposals/"
    worker=record["pre_main_commits"][0]["worker_run_id"]
    path=prefix+worker+"/task.json"
    payload=read(root,path)
    payload["sources"][WORK]["content"]="# fabricated original\n"
    payload["sources"][WORK]["sha256"]=hashlib.sha256(b"# fabricated original\n").hexdigest()
    artifact(root,path,payload)
    bad(root,record,bindings)


def test_consistently_forged_all_source_artifacts_fail_only_actual_git_source_binding(tmp_path):
    root,record,bindings=fixture(tmp_path)
    entry=record["pre_main_commits"][0]
    wb=f".local/two-site-refactor/proposals/{entry['worker_run_id']}/"
    rb=f".local/two-site-refactor/proposals/{entry['reviewer_run_id']}/"
    worker_snapshot=read(root,wb+"task.json")
    worker_snapshot["sources"][WORK]={"content":"# internally consistent forged source\n",
        "sha256":hashlib.sha256(b"# internally consistent forged source\n").hexdigest()}
    proposal=read(root,wb+"result.json")
    proposal["changes"][0]["original_sha256"]=worker_snapshot["sources"][WORK]["sha256"]
    proposal_sha=artifact(root,wb+"result.json",proposal)
    worker_status=read(root,wb+"status.json");worker_status["result_sha256"]=proposal_sha
    artifact(root,wb+"status.json",worker_status)
    artifact(root,wb+"task.json",worker_snapshot)
    tp=".local/two-site-refactor/pipeline/lab-pre/pre-review-tests-0.json"
    receipt=read(root,tp);receipt["proposal_sha256"]=proposal_sha
    receipt_sha=artifact(root,tp,receipt)
    reviewer_snapshot=read(root,rb+"task.json")
    reviewer_snapshot.update(sources=copy.deepcopy(worker_snapshot["sources"]),proposal=serialize_json(proposal).decode(),test_evidence=receipt)
    artifact(root,rb+"task.json",reviewer_snapshot)
    reviewer_status=read(root,rb+"status.json")
    reviewer_status.update(input_proposal_sha256=proposal_sha,input_test_evidence_sha256=receipt_sha)
    artifact(root,rb+"status.json",reviewer_status)
    entry.update(proposal_sha256=proposal_sha,test_evidence_sha256=receipt_sha)
    # All metadata, actual final blobs, receipts, actor inputs and graph remain
    # coherent. The actual original Git blob is the one invalid assertion.
    with patch.object(subject,"validate_run",wraps=validate_run) as lower:
        bad(root,record,bindings)
    lower.assert_not_called()
    # Independently demonstrate that omitting original-Git source identity would
    # admit this coherent forgery; do not monkeypatch the actual acceptance call.
    with patch.object(subject,"_sources",side_effect=lambda repo,parent,sources:subject._tree(repo,parent,sorted(sources))):
        good(root,record,bindings)


def test_genuine_git_regular_mode_change_requires_proposed_mode_preservation(tmp_path):
    root,record,bindings=fixture(tmp_path,unreviewed_mode=True)
    verify_main_maintenance_git_chain(root,record,**bindings)
    bad(root,record,bindings)


def test_worker_and_reviewer_same_conversation_with_all_other_inputs_bound(tmp_path):
    root,record,bindings=fixture(tmp_path)
    entry=record["main_integration"]
    worker_path=f".local/two-site-refactor/proposals/{entry['worker_run_id']}/status.json"
    reviewer_path=f".local/two-site-refactor/proposals/{entry['reviewer_run_id']}/status.json"
    worker_status=read(root,worker_path)
    reviewer_status=read(root,reviewer_path)
    reviewer_status["conversation_id"]=worker_status["conversation_id"]
    artifact(root,reviewer_path,reviewer_status)
    bad(root,record,bindings)


@pytest.mark.parametrize("kind",["proposal","receipt","review-input","review-state","receipt-selector"])
def test_actual_digest_receipt_and_independent_review_binding(tmp_path,kind):
    root,record,bindings=fixture(tmp_path)
    entry=record["post_main_commits"][0]
    if kind=="proposal":
        path=f".local/two-site-refactor/proposals/{entry['worker_run_id']}/result.json"
        payload=read(root,path);payload["changes"][0]["content"]="# hidden rewrite\n"
    elif kind.startswith("receipt"):
        path=".local/two-site-refactor/pipeline/lab-post/pre-review-tests-0.json"
        payload=read(root,path)
        if kind=="receipt-selector":payload["selectors"]=["tests/test_wrong.py"]
        else:payload["state"]="FAILED";payload["returncode"]=1
    else:
        path=f".local/two-site-refactor/proposals/{entry['reviewer_run_id']}/status.json"
        payload=read(root,path)
        if kind=="review-input":payload["input_test_evidence_sha256"]="0"*64
        else:payload["state"]="CHANGES_REQUIRED"
    good(root,record,bindings)
    original=(root/path).read_bytes()
    artifact(root,path,payload)
    bad(root,record,bindings)
    (root/path).write_bytes(original)
    good(root,record,bindings)


@pytest.mark.parametrize("fault",["raw-feedback","feedback-digest","prior-proposal","cycle"])
def test_actual_correction_chain_faults(tmp_path,fault):
    root,record,bindings=fixture(tmp_path,2)
    base=".local/two-site-refactor/proposals/lab-pre-worker-2/"
    if fault=="raw-feedback":
        path=".local/two-site-refactor/pipeline/lab-pre/feedback-correction-2.json"
        payload=read(root,path);payload["findings"]=["tampered feedback"]
    elif fault in ("feedback-digest","cycle"):
        path=base+"status.json";payload=read(root,path)
        payload["input_feedback_sha256" if fault=="feedback-digest" else "input_worker_run_id"]="0"*64 if fault=="feedback-digest" else "lab-pre-worker-2"
    else:
        path=base+"task.json";payload=read(root,path);payload["proposal"]="{}"
    good(root,record,bindings)
    original=(root/path).read_bytes()
    artifact(root,path,payload)
    bad(root,record,bindings)
    (root/path).write_bytes(original)
    good(root,record,bindings)


@pytest.mark.parametrize("fault",["symlink","hardlink","mode","oversize","missing"])
def test_actual_file_custody_rejections(tmp_path,fault):
    root,record,bindings=fixture(tmp_path)
    path=root/".local/two-site-refactor/proposals/lab-pre-worker-0/result.json"
    if fault=="symlink":
        moved=path.with_name("original.json");path.rename(moved);path.symlink_to(moved.name)
    elif fault=="hardlink":
        __import__("os").link(path,path.with_name("alias.json"))
    elif fault=="mode":path.chmod(0o644)
    elif fault=="oversize":path.write_bytes(b"x"*160001)
    else:path.unlink()
    bad(root,record,bindings)


def test_post_custody_drift_after_first_success_is_rejected(tmp_path):
    root,record,bindings=fixture(tmp_path)
    calls=0
    def mutate(*args,**kwargs):
        nonlocal calls
        calls+=1
        result=load_verifier_pre_review_receipt(*args,**kwargs)
        if calls==3:
            path=".local/two-site-refactor/proposals/lab-pre-worker-0/status.json"
            payload=read(root,path);payload["conversation_id"]="late-drift"
            artifact(root,path,payload)
        return result
    with patch.object(subject,"load_verifier_pre_review_receipt",side_effect=mutate):
        bad(root,record,bindings)
    assert calls==3


def test_cumulative_file_budget_and_bytes_fail_closed(tmp_path):
    root,record,bindings=fixture(tmp_path)
    for field,value in (("MAX_FILES",1),("MAX_BYTES",1)):
        with patch.object(subject,field,value):bad(root,record,bindings)
    good(root,record,bindings)


@pytest.mark.parametrize("exception",[RuntimeError("secret"),OSError("private"),ValueError("payload")])
def test_fixed_ordinary_errors(tmp_path,exception):
    root,record,bindings=fixture(tmp_path)
    with patch.object(subject,"validate_run",side_effect=exception):bad(root,record,bindings)


@pytest.mark.parametrize("exception",[KeyboardInterrupt(),SystemExit(2),GeneratorExit()])
def test_base_exception_identity(tmp_path,exception):
    root,record,bindings=fixture(tmp_path)
    with patch.object(subject,"validate_run",side_effect=exception):
        with pytest.raises(type(exception)) as caught:
            subject.verify_main_maintenance_evidence_locked(root,record,**bindings)
    assert caught.value is exception


def test_invalid_record_never_reads_artifacts(tmp_path):
    root,record,bindings=fixture(tmp_path)
    record["prior_checkpoint_sha256"]="0"*64
    with patch.object(subject,"bounded_file_bytes") as read_bytes:bad(root,record,bindings)
    read_bytes.assert_not_called()


def test_declared_utf8_caps():
    for path,cap in ((Path(subject.__file__),16000),(Path(__file__),24000)):
        with path.open("rb") as stream:raw=stream.read(cap+1)
        assert len(raw)==path.stat().st_size<=cap
