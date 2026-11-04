"""Reconcile ONE exact, genuinely reviewed, already applied/staged business proposal.

Not dirty-tree admission, business acceptance, a model retry or a checkpoint writer.
Only an original failed coordinator at the SAME genuine CPP/Source/policy may
qualify. The explicit transition preserves that failure and gives zero credit.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import copy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import time

KIND = "QODER_BUSINESS_EFFECT_RECONCILIATION_V1"
STATE = Path(".local/two-site-refactor/autopilot")
JOBS = Path(".local/two-site-refactor/business-reconciliation")
MAX_JOB = 1000000
MAX_RECORD = 16384
SHA = re.compile(r"[0-9a-f]{64}\Z")
HEAD = re.compile(r"[0-9a-f]{40}\Z")
JOB = re.compile(r"reconcile-[0-9a-f]{32}\Z")
KNOWN_FAILURE = "AutopilotError: integration worktree is not clean"


class BusinessReconciliationError(ValueError):
    pass


def need(value, code):
    if not value:
        raise BusinessReconciliationError(code)


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(root, path, limit=MAX_JOB):
    root, path = Path(root), Path(path)
    if not path.is_absolute(): path = root / path
    need(path.is_relative_to(root) and path.resolve(strict=True) == path, "RECONCILE_PATH_ALIAS")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
             and before.st_nlink == 1 and not before.st_mode & 0o022
             and before.st_size <= limit, "RECONCILE_FILE_CUSTODY")
        chunks = []; count = 0
        while True:
            block = os.read(fd, min(65536, limit + 1 - count))
            if not block: break
            chunks.append(block); count += len(block); need(count <= limit, "RECONCILE_FILE_BOUND")
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        after = os.fstat(fd); final = path.lstat()
        need(count == before.st_size and all(getattr(before,k) == getattr(after,k) == getattr(final,k) for k in fields),
             "RECONCILE_FILE_DRIFT")
        return b"".join(chunks)
    finally:
        os.close(fd)


def decode(raw):
    from scripts.two_site_qoder_driver import _json
    value = _json(raw)
    need(type(value) is dict, "RECONCILE_OBJECT")
    return value


def obj(root, path, limit=MAX_JOB):
    return decode(read(root, path, limit))


def new(path, value):
    """Durable no-replace record; an identical retained phase is never rewritten."""
    raw = value if type(value) is bytes else canonical(value)
    need(Path(path).parent.resolve(strict=True) == Path(path).parent, "RECONCILE_DIRECTORY_ALIAS")
    if os.path.lexists(path):
        need(read(Path(path).parent, path) == raw, "RECONCILE_PHASE_DRIFT")
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def no_stop(root):
    need(not any(os.path.lexists(Path(root)/p) for p in ("STOP",STATE/"STOP")), "RECONCILE_STOP")


def git(root, *args, mutation=False, limit=2000000):
    from scripts.two_site_agent_sandbox import _run_bounded_process
    environment = {"PATH":"/usr/bin:/bin", "LANG":"C.UTF-8", "GIT_TERMINAL_PROMPT":"0", "GIT_NO_LAZY_FETCH":"1"}
    for name in ("HOME","XDG_CONFIG_HOME","GNUPGHOME","GPG_TTY"):
        if name in os.environ: environment[name] = os.environ[name]
    command = ["/usr/bin/git", "--no-replace-objects", "-C", str(root), *args]
    result = _run_bounded_process(command, cwd=Path(root), env=environment,
                                  timeout=120 if mutation else 20, output_limit=limit)
    need(result.returncode == 0, "RECONCILE_GIT_EFFECT_FAILED" if mutation else "RECONCILE_GIT_QUERY_FAILED")
    return result.stdout


def checkpoint(root):
    from scripts import two_site_codex_autopilot as ap
    value = ap._load_accepted_checkpoint(Path(root)/STATE)
    need(value is not None, "RECONCILE_GENUINE_CPP_REQUIRED")
    return value, sha(read(root, STATE/ap.ACCEPTED_CHECKPOINT_FILENAME))


def repository(root, *, dirty=False):
    from scripts import two_site_codex_autopilot as ap
    value = ap.validate_repository(Path(root), require_clean=not dirty)
    detached = dict(value, status="") if dirty else value
    return ap.validate_accepted_repository(detached)


def _idle(root):
    from scripts import two_site_provider_broker as broker
    base = Path(root)/broker.STATE_REL
    status = obj(root, base/"status.json", 65536)
    need(status.get("state") == "IDLE" and status.get("qoder_final_review_required") is True,
         "RECONCILE_BROKER_NOT_IDLE")
    for name in ("requests","claimed"):
        need((base/name).is_dir() and (base/name).resolve(strict=True) == base/name
             and not any((base/name).iterdir()), "RECONCILE_PENDING_PROVIDER")


def _dead_original(meta):
    pid = meta.get("pid")
    need(type(pid) is int and pid > 1, "RECONCILE_ORIGINAL_PROCESS_UNKNOWN")
    try: os.kill(pid, 0)
    except ProcessLookupError: pass
    except PermissionError: raise BusinessReconciliationError("RECONCILE_ORIGINAL_PROCESS_UNKNOWN") from None
    else: raise BusinessReconciliationError("RECONCILE_ORIGINAL_PROCESS_LIVE_OR_REUSED")
    # The coordinator is created with start_new_session=True, so its PID is the
    # process-group ID.  A dead parent is not quiescent while any descendant in
    # that owned group survives.
    try: os.killpg(pid, 0)
    except ProcessLookupError: return
    except PermissionError: raise BusinessReconciliationError("RECONCILE_ORIGINAL_PROCESS_UNKNOWN") from None
    raise BusinessReconciliationError("RECONCILE_ORIGINAL_PROCESS_GROUP_LIVE")


def _source_controls(root, before):
    from scripts import two_site_codex_autopilot as ap
    for name in before["control_blobs"]:
        need(read(root, name, 2500000) == ap.git_blob(Path(root), before["head"], name),
             "RECONCILE_PHYSICAL_CONTROL_DRIFT")


def _original(root, directory, before, *, live_check):
    from scripts import two_site_ai_config as ai, two_site_codex_autopilot as ap
    from scripts import two_site_qoder_driver as qoder, two_site_provider_broker as broker
    from scripts.two_site_coordinator_broker_wait import _same_request
    directory = Path(directory)
    if not directory.is_absolute(): directory = Path(root)/directory
    need(directory.parent == Path(root)/STATE/"runs" and ap.RUN_ID.fullmatch(directory.name)
         and directory.resolve(strict=True) == directory, "RECONCILE_CONTROLLER_PATH")
    meta = obj(root, directory/"status.json")
    need(meta.get("state") == "FAILED" and meta.get("error") == KNOWN_FAILURE
         and meta.get("coordinator_provider") == "qoder", "RECONCILE_FAILURE_NOT_QUALIFIED")
    need(meta.get("head_before") == before["head"] and meta.get("protected_refs") == before["protected_refs"],
         "RECONCILE_ORIGINAL_SOURCE_DRIFT")
    scope = before["assignment_scope"]
    need(meta.get("assignment_scope") == {k:scope[k] for k in ("assignment_id","stage_id","sha256")},
         "RECONCILE_ORIGINAL_SCOPE_DRIFT")
    config = ai.load_ai_config(root); runtime = ai.load_runtime_config(root)
    need(meta.get("model_config") == config, "RECONCILE_MODEL_POLICY_DRIFT")
    identity = meta.get("coordinator", {})
    role = config["roles"]["coordinator"]
    need(all(identity.get(k) == runtime["coordinator"][k] for k in ("path","version","sha256"))
         and identity.get("provider") == "qoder" and identity.get("logged_in") is True
         and identity.get("auto_update_enabled") is False and identity.get("model") == role["model"]
         and identity.get("reasoning") == role["reasoning_effort"]
         and ap.file_sha256(Path(runtime["coordinator"]["path"])) == identity["sha256"],
         "RECONCILE_COORDINATOR_IDENTITY_DRIFT")
    terminal = qoder.validate_qoder_terminal(directory, expected_model=role["model"])
    if live_check: _dead_original(meta)
    reservation = broker.read_cycle_reservation(Path(root), directory.name)
    dispatch = obj(root, directory/broker._CYCLE_DISPATCH, broker._MAX_CYCLE_RECORD)
    need(set(dispatch) == {"schema_version","request","broker_pid","broker_started_at"}
         and dispatch["schema_version"] == "1.0" and type(dispatch["broker_pid"]) is int
         and dispatch["broker_pid"] > 1 and type(dispatch["broker_started_at"]) in (int,float)
         and math.isfinite(dispatch["broker_started_at"]), "RECONCILE_DISPATCH")
    request = dispatch["request"]; _same_request(reservation,request)
    need(request["controller_run_id"] == directory.name and request.get("request_type") is None
         and request["source_head"] == before["head"]
         and request["assignment_id"] == scope["assignment_id"] and request["assignment_sha256"] == scope["sha256"],
         "RECONCILE_REQUEST_BINDING")
    wrap = obj(root,directory/"broker-terminal-response.json",broker.MAX_PIPELINE_OUTPUT_BYTES+65536)
    need(set(wrap)=={"schema_version","request","response"} and wrap["schema_version"]=="1.0"
         and wrap["request"]==request,"RECONCILE_TERMINAL_BINDING")
    response=broker.validate_response(request,wrap["response"])
    retained=obj(root,Path(broker.STATE_REL)/"responses"/(request["request_id"]+".json"),broker.MAX_PIPELINE_OUTPUT_BYTES+65536)
    need(retained==response and response["state"]=="COMPLETED" and response["exit_code"]==0
         and response["stderr"]=="" and math.isfinite(response["finished_at"]),"RECONCILE_BROKER_NOT_READY")
    ready=decode(response["stdout"])
    need(ready.get("state")=="READY_FOR_CODEX" and ready.get("source_head")==before["head"]
         and ready.get("blockers")==[] and ready.get("pre_review_test_state")=="PASSED",
         "RECONCILE_READY_SHAPE")
    return directory,request,ready,terminal


def _proof(root, before, directory, *, live_check=False):
    from scripts import two_site_codex_autopilot as ap, two_site_qoder_review as final
    from scripts import two_site_proposal_runner as runner
    from scripts.two_site_assignment_scope import load_active_assignment_scope,validate_task_scope
    from scripts.two_site_pre_review_receipt import validate_test_receipt
    from scripts.two_site_semantic_attempts import read_existing_semantic_attempt
    directory,request,ready,terminal=_original(root,directory,before,live_check=live_check)
    task_path=Path(root)/request["task_path"]
    task_raw=read(root,task_path,runner.MAX_CONTEXT);task=decode(task_raw)
    need(sha(task_raw)==request["task_sha256"] and task["task_id"]==ready.get("task_id"),"RECONCILE_TASK_DRIFT")
    active=load_active_assignment_scope(Path(root));validate_task_scope(active,task)
    need(ap.assignment_scope_context(active)==before["assignment_scope"],"RECONCILE_SCOPE_DRIFT")
    binding={"gemini_worker_run_id":ready.get("final_worker_run_id"),
             "gemini_reviewer_run_id":ready.get("final_reviewer_run_id"),"proposal_sha256":ready.get("proposal_sha256")}
    need(all(type(binding[key])is str and ap.PROPOSAL_RUN_ID.fullmatch(binding[key])
             for key in ("gemini_worker_run_id","gemini_reviewer_run_id"))
         and type(binding["proposal_sha256"])is str and SHA.fullmatch(binding["proposal_sha256"]),
         "RECONCILE_ROLE_BINDINGS")
    proposal=ap.validate_scoped_gemini_chain(Path(root),before["head"],binding,active)
    paths=sorted(c["path"] for c in proposal["changes"])
    need(ready.get("changed_paths")==[c["path"] for c in proposal["changes"]]
         and not set(paths).intersection(before["control_blobs"]),"RECONCILE_PATH_OR_CONTROL_DRIFT")
    worker=Path(root)/".local/two-site-refactor/proposals"/binding["gemini_worker_run_id"]
    reviewer=worker.parent/binding["gemini_reviewer_run_id"]
    need(ready.get("proposal_path")==str((worker/"result.json").relative_to(root))
         and ready.get("reviewer_path")==str((reviewer/"result.json").relative_to(root)),"RECONCILE_RESULT_PATH")
    snapshot=obj(root,worker/"task.json",3*runner.MAX_CONTEXT)
    need(snapshot["task"]==task and snapshot.get("maintenance_authorization") is None,"RECONCILE_NOT_BUSINESS")
    sources=snapshot["sources"]
    need(set(sources)==set(task["context_files"]+task["allowed_files"]),"RECONCILE_SOURCE_SET")
    for name,source in sources.items():
        runner.relative_file(Path(root),name)
        need(type(source)is dict and set(source)=={"sha256","content"},"RECONCILE_SOURCE_SHAPE")
        mode=ap.tree_mode(Path(root),before["head"],name)
        if source["sha256"] is None:
            need(source["content"] is None and mode is None and name in task["allowed_files"],"RECONCILE_NEW_SOURCE")
        else:
            need(type(source["content"])is str and mode in {"100644","100755"}
                 and sha(source["content"].encode())==source["sha256"]
                 and ap.git_blob(Path(root),before["head"],name)==source["content"].encode(),"RECONCILE_ORIGINAL_BLOB")
    final_receipt=final.validate_review_receipt(Path(root),task_path,binding["gemini_worker_run_id"],
        binding["gemini_reviewer_run_id"],binding["proposal_sha256"],expected_source_head=before["head"],require_approve=True)
    need(final_receipt["proof"]["session_id"]!=terminal["session_id"],"RECONCILE_COORDINATOR_SELF_REVIEW")
    count=ready.get("correction_count")
    need(type(count)is int and 0<=count<=2,"RECONCILE_CORRECTION_INDEX")
    native_path=Path(".local/two-site-refactor/pipeline")/request["run_prefix"]/f"pre-review-tests-{count}.json"
    native_raw=read(root,native_path,runner.MAX_CONTEXT);native=validate_test_receipt(decode(native_raw),binding["proposal_sha256"],require_passed=True)
    review_snapshot=obj(root,reviewer/"task.json",3*runner.MAX_CONTEXT)
    need(ready.get("pre_review_test_path")==native_path.as_posix()
         and ready.get("pre_review_test_sha256")==sha(native_raw) and native["selectors"]==task["test_selectors"]
         and review_snapshot["test_evidence"]==native,"RECONCILE_NATIVE_DRIFT")
    semantic=read_existing_semantic_attempt(Path(root),Path(root)/".local/two-site-refactor/pipeline",task,request["run_prefix"])
    need(semantic["fingerprint"]==ready.get("semantic_task_fingerprint"),"RECONCILE_SEMANTIC_RESERVATION")
    pins={}
    for path in (directory/"status.json",directory/"stream.jsonl",directory/"stderr.txt",directory/"broker-reservation.json",
                 directory/"preparation-startup.json",directory/"broker-dispatch.json",directory/"broker-terminal-response.json",
                 Path(root)/Path(__import__('scripts.two_site_provider_broker',fromlist=['STATE_REL']).STATE_REL)/"responses"/(request["request_id"]+".json"),
                 task_path,Path(root)/native_path):
        body=read(root,path,16000000);pins[str(path.relative_to(root))]=sha(body)
    return {"failed_cycle":directory.relative_to(root).as_posix(),"request":request,"ready":ready,
        "task":task,"sources":sources,"proposal":proposal,"binding":binding,"paths":paths,
        "native_sha256":sha(native_raw),"proof_pins":pins,
        "final_receipt_sha256":sha(read(root,Path(final.REVIEW_REL)/(binding["proposal_sha256"]+".json")))}


def _effects(root, before, proof, *, owned=False, committed=False):
    from scripts import two_site_codex_autopilot as ap
    paths=proof["paths"];changes={c["path"]:c for c in proof["proposal"]["changes"]}
    dirty=set()
    for args in (("diff","--name-only","--no-renames","-z"),("diff","--cached","--name-only","--no-renames","-z"),
                 ("ls-files","--others","--exclude-standard","-z")):
        dirty.update(p.decode() for p in git(root,*args).split(b"\0") if p)
    need(not dirty if committed else dirty==set(paths),"RECONCILE_FOREIGN_OR_MISSING_EFFECT")
    result={}
    for name in paths:
        target=Path(root)/name;info=target.lstat();expected=changes[name]["content"].encode()
        need(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.geteuid()
             and not info.st_mode & 0o7022 and read(root,target,160000)==expected,"RECONCILE_WORKTREE_NOT_PROPOSAL")
        mode="100755" if info.st_mode & 0o111 else "100644"
        old_mode=ap.tree_mode(Path(root),before["head"],name)
        need(mode==(old_mode or "100644"),"RECONCILE_MODE_DRIFT")
        listing=git(root,"ls-files","--stage","-z","--",name).split(b"\0");listing=[x for x in listing if x]
        if listing:
            need(len(listing)==1 and listing[0].endswith(b"\t"+name.encode()),"RECONCILE_INDEX_CONFLICT")
            metadata=listing[0].split(b"\t",1)[0].split()
            need(len(metadata)==3 and metadata[2]==b"0", "RECONCILE_INDEX_CONFLICT")
            index=git(root,"show",":"+name);index_mode=metadata[0].decode()
        else:index=None;index_mode=None
        index_after=index==expected and index_mode==mode
        source=proof["sources"][name]
        index_before=index==(source["content"].encode() if source["content"] is not None else None) and index_mode==old_mode
        need(index_after or owned and not committed and index_before,"RECONCILE_INDEX_NOT_PROPOSAL")
        result[name]={"posix_mode":stat.S_IMODE(info.st_mode),"git_mode":mode,"after_sha256":sha(expected)}
    return result


def classify_business_reconciliation(root, failed_cycle_dir):
    """Read-only, called under AP. None only for an unrelated terminal category."""
    root=Path(root).resolve(strict=True);directory=Path(failed_cycle_dir)
    if not directory.is_absolute():directory=root/directory
    from scripts import two_site_codex_autopilot as ap
    need(directory.parent==root/STATE/"runs" and ap.RUN_ID.fullmatch(directory.name)
         and directory.resolve(strict=True)==directory,"RECONCILE_CONTROLLER_PATH")
    info=directory.lstat()
    need(stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid()
         and stat.S_IMODE(info.st_mode)==0o700,"RECONCILE_CONTROLLER_CUSTODY")
    meta=obj(root,directory/"status.json")
    if meta.get("state")!="FAILED" or meta.get("error")!=KNOWN_FAILURE:return None
    no_stop(root);before=repository(root,dirty=True);cp,cp_sha=checkpoint(root)
    need(cp["repository"]==before,"RECONCILE_CPP_OR_SOURCE_DRIFT")
    _source_controls(root,before);_idle(root)
    proof=_proof(root,before,directory,live_check=True);effects=_effects(root,before,proof)
    need(repository(root,dirty=True)==before and checkpoint(root)[1]==cp_sha,"RECONCILE_ADMISSION_DRIFT")
    return {"schema_version":1,"kind":KIND,"before_repository":before,"checkpoint_sha256":cp_sha,
            "proof":proof,"effects":effects,"business_credit":0,"clean_credit":0}


def prepare_business_reconciliation(root, failed_cycle_dir):
    """Caller owns all holds. Write only an immutable journal, never new work."""
    root=Path(root);job=classify_business_reconciliation(root,failed_cycle_dir)
    need(job is not None,"RECONCILE_FAILURE_NOT_QUALIFIED")
    job_id="reconcile-"+sha(canonical(job))[:32];directory=root/JOBS/job_id
    directory.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    need(directory.parent.resolve(strict=True)==directory.parent,"RECONCILE_DIRECTORY_ALIAS")
    if not os.path.lexists(directory):directory.mkdir(mode=0o700)
    new(directory/"job.json",job)
    return {"kind":KIND,"job_id":job_id,"sha256":sha(read(root,directory/"job.json"))}


def _job(root, authorization):
    need(type(authorization)is dict and set(authorization)=={"kind","job_id","sha256"}
         and authorization["kind"]==KIND and type(authorization["job_id"])is str and JOB.fullmatch(authorization["job_id"])
         and type(authorization["sha256"])is str and SHA.fullmatch(authorization["sha256"]),"RECONCILE_AUTHORIZATION")
    directory=Path(root)/JOBS/authorization["job_id"];raw=read(root,directory/"job.json")
    need(sha(raw)==authorization["sha256"],"RECONCILE_JOB_DRIFT")
    job=decode(raw);need(raw==canonical(job) and authorization["job_id"]=="reconcile-"+sha(raw)[:32],"RECONCILE_JOB_ID")
    need(set(job)=={"schema_version","kind","before_repository","checkpoint_sha256","proof","effects","business_credit","clean_credit"}
         and type(job["schema_version"])is int and job["schema_version"]==1 and job["kind"]==KIND
         and type(job["business_credit"])is int and job["business_credit"]==0
         and type(job["clean_credit"])is int and job["clean_credit"]==0,"RECONCILE_JOB_SCHEMA")
    cp,cp_sha=checkpoint(root);need(cp_sha==job["checkpoint_sha256"] and cp["repository"]==job["before_repository"],"RECONCILE_CPP_DRIFT")
    return directory,job


def _validate_job(root, authorization, *, live_check=False):
    from scripts import two_site_codex_autopilot as ap
    directory,job=_job(root,authorization);no_stop(root);before=job["before_repository"]
    current=repository(root,dirty=True);expected=copy.deepcopy(before);expected["head"]=current["head"]
    need(current==expected,"RECONCILE_REPOSITORY_DRIFT")
    _source_controls(root,before)
    proof=_proof(root,before,Path(root)/job["proof"]["failed_cycle"],live_check=live_check)
    need(canonical(proof)==canonical(job["proof"]),"RECONCILE_PROOF_DRIFT")
    committed=current["head"]!=before["head"]
    if committed:ap.validate_scoped_committed_proposal(Path(root),before["head"],current["head"],proof["proposal"],
                                                      __import__('scripts.two_site_assignment_scope',fromlist=['load_active_assignment_scope']).load_active_assignment_scope(root))
    effects=_effects(root,before,proof,owned=True,committed=committed)
    need(effects==job["effects"],"RECONCILE_EFFECT_CUSTODY_DRIFT")
    return directory,job,current,committed


def finish_business_reconciliation(root, authorization):
    """Caller owns all holds; exact index-only repair, same bytes, ordinary Git."""
    from scripts import two_site_cycle_tools as ct
    root=Path(root);_idle(root)
    directory,job,current,committed=_validate_job(root,authorization,live_check=True)
    proof=job["proof"];paths=proof["paths"];before=job["before_repository"]
    if not committed:
        no_stop(root)
        # A new path already removed from the index cannot be passed to
        # git restore a second time. Only exact retained after-index entries
        # need this index-only transition; _validate_job proved the rest old.
        staged=[name.decode() for name in git(root,"diff","--cached","--name-only","--no-renames","-z").split(b"\0") if name]
        need(set(staged).issubset(paths),"RECONCILE_FOREIGN_INDEX_EFFECT")
        if staged:
            git(root,"restore","--staged","--source="+before["head"],"--",*staged,mutation=True)
        _validate_job(root,authorization)
        new(directory/"index-unstaged.json",{"job_sha256":authorization["sha256"],"paths":paths})
        receipt=ct.review(root,root/proof["request"]["task_path"],proof["binding"]["gemini_worker_run_id"],
            proof["binding"]["gemini_reviewer_run_id"],proof["binding"]["proposal_sha256"],applied=True)
        need(receipt["state"]=="BYTES_VALID","RECONCILE_APPLIED_REVIEW_FAILED")
        new(directory/"applied-review.json",receipt)
        no_stop(root);git(root,"add","--",*paths,mutation=True);_validate_job(root,authorization)
        new(directory/"index-restaged.json",{"job_sha256":authorization["sha256"],"paths":paths})
        message=directory/"commit-message.txt"
        new(message,("refactor: reconcile exact reviewed business effects "+authorization["job_id"]+"\n").encode())
        no_stop(root);git(root,"commit","--only","--file",str(message),"--",*paths,mutation=True)
    directory,job,current,committed=_validate_job(root,authorization)
    need(committed,"RECONCILE_COMMIT_NOT_OBSERVED")
    value=_execution(authorization,job,current)
    new(directory/"execution.json",value)
    return value


def _execution(authorization,job,current):
    proof=job["proof"];paths=proof["paths"];before=job["before_repository"]
    value={"schema_version":1,"kind":KIND,"job_id":authorization["job_id"],"job_sha256":authorization["sha256"],
        "head_before":before["head"],"head_after":current["head"],"changed_paths":paths,
        "worker_run_id":proof["binding"]["gemini_worker_run_id"],"reviewer_run_id":proof["binding"]["gemini_reviewer_run_id"],
        "proposal_sha256":proof["binding"]["proposal_sha256"],"native_sha256":proof["native_sha256"],
        "original_failed_controller":Path(proof["failed_cycle"]).name,"business_credit":0,"clean_credit":0,
        "normal_checkpoint_written":False,"new_repository":current}
    return value


def validate_business_transition_record(value):
    """Pure closed profile; never expands the old maintenance path whitelist."""
    from scripts import two_site_codex_autopilot as ap
    need(type(value)is dict and set(value)=={"schema_version","kind","prior_checkpoint_sha256","prior_head",
         "new_repository","maintenance_commits","evidence"} and value["schema_version"]=="1.0" and value["kind"]==KIND,
         "RECONCILE_TRANSITION_SCHEMA")
    need(type(value["prior_checkpoint_sha256"])is str and SHA.fullmatch(value["prior_checkpoint_sha256"])
         and type(value["prior_head"])is str and HEAD.fullmatch(value["prior_head"]),"RECONCILE_TRANSITION_HASH")
    repo=ap._validate_persisted_repository(value["new_repository"])
    rows=value["maintenance_commits"];evidence=value["evidence"]
    need(type(rows)is list and len(rows)==1 and type(evidence)is list and len(evidence)==1,"RECONCILE_SINGLE_COMMIT")
    row=rows[0];proof=evidence[0]
    need(type(row)is dict and set(row)=={"head","parent","paths","worker_run_id","reviewer_run_id","proposal_sha256","test_evidence_sha256"}
         and type(row["head"])is str and HEAD.fullmatch(row["head"]) and row["head"]==repo["head"]
         and row["parent"]==value["prior_head"] and row["head"]!=row["parent"],"RECONCILE_TRANSITION_COMMIT")
    for key in ("worker_run_id","reviewer_run_id"):
        need(type(row[key])is str and ap.PROPOSAL_RUN_ID.fullmatch(row[key]),"RECONCILE_TRANSITION_ROLE")
    need(row["worker_run_id"]!=row["reviewer_run_id"] and all(type(row[k])is str and SHA.fullmatch(row[k])
         for k in ("proposal_sha256","test_evidence_sha256")),"RECONCILE_TRANSITION_PROOF")
    paths=row["paths"]
    need(type(paths)is list and 0<len(paths)<=32 and all(type(p)is str for p in paths)
         and paths==sorted(set(paths)) and all(type(p)is str
         and p and not PurePosixPath(p).is_absolute() and not {"..",".",""}.intersection(p.split("/"))
         and not any(ord(c)<32 for c in p) and "\\" not in p for p in paths),"RECONCILE_TRANSITION_PATHS")
    need(type(proof)is dict and set(proof)=={"job_id","execution_receipt_sha256"}
         and type(proof["job_id"])is str and JOB.fullmatch(proof["job_id"])
         and type(proof["execution_receipt_sha256"])is str and SHA.fullmatch(proof["execution_receipt_sha256"])
         and len(canonical(value))<=MAX_RECORD,"RECONCILE_TRANSITION_EVIDENCE")
    return copy.deepcopy(value)


def _record(root, execution_path):
    from scripts import two_site_codex_autopilot as ap
    path=Path(execution_path)
    need(path.name=="execution.json" and path.parent.parent==Path(root)/JOBS and JOB.fullmatch(path.parent.name),"RECONCILE_EXECUTION_PATH")
    raw=read(root,path);execution=decode(raw)
    authorization={"kind":KIND,"job_id":path.parent.name,"sha256":execution.get("job_sha256")}
    directory,job,current,committed=_validate_job(root,authorization)
    need(committed and raw==canonical(execution)==canonical(_execution(authorization,job,current)),"RECONCILE_EXECUTION_DRIFT")
    proof=job["proof"];before=job["before_repository"]
    return validate_business_transition_record({"schema_version":"1.0","kind":KIND,
        "prior_checkpoint_sha256":job["checkpoint_sha256"],"prior_head":before["head"],"new_repository":current,
        "maintenance_commits":[{"head":current["head"],"parent":before["head"],"paths":proof["paths"],
            "worker_run_id":execution["worker_run_id"],"reviewer_run_id":execution["reviewer_run_id"],
            "proposal_sha256":execution["proposal_sha256"],"test_evidence_sha256":execution["native_sha256"]}],
        "evidence":[{"job_id":authorization["job_id"],"execution_receipt_sha256":sha(raw)}]})


def publish_business_reconciliation_transition_locked(root, execution_path):
    """Caller owns all holds; no CPP write, business count or normal acceptance."""
    from scripts import two_site_codex_autopilot as ap
    from scripts.two_site_maintenance_transition_file import publish_maintenance_transition_bytes_noreplace
    from scripts.two_site_maintenance_transition_dir import open_maintenance_transitions_dir
    root=Path(root).resolve(strict=True);no_stop(root);_idle(root)
    record=_record(root,execution_path);raw=canonical(record)
    state=root/STATE;fd=os.open(state,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    trans=None
    try:
        trans=open_maintenance_transitions_dir(fd,create=True)
        leaf=ap.rescope_epoch_basename(record["new_repository"])+".json"
        if os.path.lexists(state/"maintenance-transitions"/leaf):
            need(read(root,state/"maintenance-transitions"/leaf,MAX_RECORD)==raw,"RECONCILE_TRANSITION_ALREADY_DIFFERENT")
        else:publish_maintenance_transition_bytes_noreplace(trans,leaf,raw)
    finally:
        if trans is not None:os.close(trans)
        os.close(fd)
    need(_record(root,execution_path)==record,"RECONCILE_PUBLICATION_DRIFT");no_stop(root)
    return record


def read_business_reconciliation_transition_locked(root,state_root):
    from scripts import two_site_codex_autopilot as ap
    root=Path(root).resolve(strict=True);need(Path(state_root)==root/STATE,"RECONCILE_STATE_ROOT")
    current=repository(root);path=Path(state_root)/"maintenance-transitions"/(ap.rescope_epoch_basename(current)+".json")
    if not os.path.lexists(path):return None
    raw=read(root,path,MAX_RECORD);value=decode(raw)
    if value.get("kind")!=KIND:return None
    record=validate_business_transition_record(value)
    need(raw==canonical(record),"RECONCILE_TRANSITION_CANONICAL")
    proof=record["evidence"][0];execution=root/JOBS/proof["job_id"]/"execution.json"
    need(sha(read(root,execution))==proof["execution_receipt_sha256"] and _record(root,execution)==record
         and record["new_repository"]==current,"RECONCILE_TRANSITION_CUSTODY")
    return record


@contextmanager
def reconciliation_holds(root):
    from scripts import two_site_codex_autopilot as ap,two_site_provider_broker as broker
    from scripts import two_site_proposal_pipeline as pipeline,two_site_proposal_runner as runner
    root=Path(root);lock=root/".local/two-site-refactor/monitor/monitor.lock"
    lock.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    need(lock.parent.resolve(strict=True)==lock.parent,"RECONCILE_MON_DIRECTORY_ALIAS")
    fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        info=os.fstat(fd);need(stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and info.st_nlink==1
             and not info.st_mode & 0o022,"RECONCILE_MON_CUSTODY")
        deadline=time.monotonic()+10
        while True:
            no_stop(root)
            try:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                need(time.monotonic()<deadline,"RECONCILE_MON_BUSY");time.sleep(.1)
        with ExitStack() as stack:
            stack.enter_context(ap.exclusive_supervisor_lock(root/STATE/"autopilot.lock"))
            stack.enter_context(broker.exclusive_broker_lock(root/broker.STATE_REL/"client.lock"))
            stack.enter_context(pipeline.pipeline_lock(root))
            descriptor,_=runner.acquire_proposal_lock(root);stack.callback(runner.release_proposal_lock,descriptor)
            no_stop(root);_idle(root);yield stack
    finally:os.close(fd)


def pending_business_reconciliation(root):
    root=Path(root);base=root/JOBS
    if not os.path.lexists(base):return None
    cp,cp_sha=checkpoint(root);jobs=[]
    for index,directory in enumerate(sorted(base.iterdir())):
        need(index<256 and JOB.fullmatch(directory.name) and directory.is_dir() and directory.resolve(strict=True)==directory,
             "RECONCILE_JOB_DIRECTORY")
        path=directory/"job.json";raw=read(root,path);job=decode(raw)
        if job.get("checkpoint_sha256")==cp_sha:
            jobs.append({"kind":KIND,"job_id":directory.name,"sha256":sha(raw)})
    need(len(jobs)<=1,"RECONCILE_AMBIGUOUS_JOB")
    return jobs[0] if jobs else None


def recover_business_reconciliation(root):
    """Trusted host entry; owns all holds and never accepts a model descriptor."""
    root=Path(root).resolve(strict=True)
    with reconciliation_holds(root) as effects_holds:
        auth=pending_business_reconciliation(root)
        if auth is None:
            status=obj(root,STATE/"status.json");evidence=status.get("evidence")
            need(type(evidence)is str,"RECONCILE_NO_FAILED_CONTROLLER")
            auth=prepare_business_reconciliation(root,Path(evidence))
        execution=finish_business_reconciliation(root,auth)
        record=publish_business_reconciliation_transition_locked(root,root/JOBS/auth["job_id"]/"execution.json")
        # MON remains held; release AP/client/pipeline/Runner before the ordinary
        # launcher acquires AP itself. The launcher retains its own fresh gates.
        effects_holds.close()
        no_stop(root)
        need(read_business_reconciliation_transition_locked(root,root/STATE)==record,
             "RECONCILE_HANDOFF_SOURCE_DRIFT")
        from scripts.two_site_agent_sandbox import _run_bounded_process
        command=["/usr/bin/bash",str(root/"scripts/two_site_autopilot_tmux.sh"),"start","--quota-wait-enabled"]
        launched=_run_bounded_process(command,cwd=root,env=os.environ.copy(),timeout=90,output_limit=65536)
        no_stop(root)
        need(launched.returncode==0,"RECONCILE_ORDINARY_HANDOFF_FAILED")
        return {"state":"REVIEWED_SOURCE_TRANSITION","kind":KIND,"business_credit":0,"clean_credit":0,
                "normal_checkpoint_written":False,"execution":execution,"record":record,
                "ordinary_handoff":{"state":"LAUNCH_ACCEPTED","returncode":0}}
