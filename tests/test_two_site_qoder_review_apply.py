"""Exercise final-review proof and exact-byte apply with a synthetic local CLI."""
import copy
import hashlib
import json
from pathlib import Path
import stat

import pytest

from scripts import two_site_ai_config as config
from scripts import two_site_apply_reviewed as apply
from scripts import two_site_proposal_runner as runner
from scripts import two_site_qoder_review as review


def put(path, value, *, raw=False, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if raw else runner.serialize_json(value)); path.chmod(mode)


@pytest.fixture
def unit(tmp_path, monkeypatch):
    root = tmp_path; head = "a" * 40; worker = "synthetic-worker"; verifier = "synthetic-reviewer"
    template = Path(__file__).resolve().parents[1] / config.AI_CONFIG_REL
    put(root / config.AI_CONFIG_REL, template.read_bytes(), raw=True)
    binary = root / "fake-qodercli"
    binary.write_text('''#!/usr/bin/python3
import json,sys
if '--version' in sys.argv: print('1.2.3')
elif 'status' in sys.argv: print(json.dumps({'logged_in':True}))
else:
 data=json.loads(sys.stdin.read().rsplit('\\n',1)[1])
 model=sys.argv[sys.argv.index('--model')+1]
 result={'decision':'APPROVE','findings':[],'proposal_sha256':data['proposal_sha256'],'source_head':data['source_head']}
 print(json.dumps({'type':'system','subtype':'init','session_id':'synthetic-final-session','model':model,'tools':['mcp_list'],'mcp_servers':[],'plugins':[]}))
 print(json.dumps({'type':'result','subtype':'success','is_error':False,'stop_reason':'end_turn','session_id':'synthetic-final-session','num_turns':1,'result':json.dumps(result)}))
'''); binary.chmod(0o700)
    runtime = {"schema_version": 1, "coordinator": {"path": str(binary), "version": "1.2.3",
        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}}
    put(root / config.AI_RUNTIME_REL, runtime)
    task_path = Path(".local/two-site-refactor/tasks/synthetic.json")
    old = "VALUE = 'قدیم\u200cدقیق'\n"; updated = "VALUE = 'جدید\u200cدقیق'\n"
    new = "NEW = 'بدون تغییر بایت'\n"
    put(root / "tests/test_old.py", old.encode(), raw=True, mode=0o750)
    sources = {"tests/test_old.py": {"sha256": runner.sha(old.encode()), "content": old},
               "tests/test_new.py": {"sha256": None, "content": None}}
    task = {"task_id": "synthetic-final-test", "goal": "Keep exact bytes", "context_files": ["tests/test_old.py"],
        "allowed_files": list(sources), "requirements": ["Preserve bytes"], "test_selectors": ["tests/test_old.py::test_one"]}
    put(root / task_path, task)
    changes = [{"path": name, "original_sha256": sources[name]["sha256"], "content": content}
               for name, content in (("tests/test_old.py", updated), ("tests/test_new.py", new))]
    worker_result = {"status": "PROPOSAL_READY", "summary": "Synthetic", "covered_requirements": [1],
                     "findings": [], "changes": changes}
    worker_raw = runner.serialize_json(worker_result); sha = runner.sha(worker_raw)
    native = {"state": "PASSED", "proposal_sha256": sha, "selectors": task["test_selectors"],
        "unsupported_specs": [], "returncode": 0, "stdout_tail": "", "stderr_tail": "",
        "snapshot_files": 1, "snapshot_bytes": 100, "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all"}
    snapshot = {"task": task, "sources": sources, "source_head": head}
    reviewer_result = {"status": "VERIFIED", "summary": "Synthetic review", "covered_requirements": [1],
                       "findings": [], "changes": []}
    proposal_dir = root / ".local/two-site-refactor/proposals"
    put(proposal_dir / worker / "result.json", worker_raw, raw=True)
    put(proposal_dir / worker / "task.json", snapshot)
    put(proposal_dir / worker / "status.json", {"conversation_id": "synthetic-worker-session"})
    put(proposal_dir / verifier / "status.json", {"conversation_id": "synthetic-reviewer-session"})
    put(proposal_dir / verifier / "result.json", reviewer_result)
    put(proposal_dir / verifier / "task.json", {**snapshot, "proposal": worker_raw.decode(), "test_evidence": native})
    binding = {"head": head, "proposal_sha256": sha,
               "worker_result": str(proposal_dir / worker / "result.json"),
               "reviewer_result": str(proposal_dir / verifier / "result.json")}
    materialized = [{"path": item["path"], "is_new": sources[item["path"]]["sha256"] is None,
        "orig_sha": sources[item["path"]]["sha256"], "orig_content": sources[item["path"]]["content"],
        "new_content": item["content"]} for item in changes]
    prepared = (binding, snapshot, worker_result, reviewer_result, materialized)
    monkeypatch.setattr(review.packet, "_prepare_proposal", lambda *args: copy.deepcopy(prepared))
    monkeypatch.setattr(review, "_gemini_chain", lambda *args: copy.deepcopy(worker_result))
    monkeypatch.setenv("TWO_SITE_COORDINATOR_SANDBOX", "1")  # Synthetic local CLI, never a real model.
    def postcheck(_root, _task, _worker, _verifier, _sha, applied=False):
        assert applied is True
        for item in materialized: assert (root / item["path"]).read_bytes() == item["new_content"].encode()
        return {"state": "BYTES_VALID", "head": head}
    monkeypatch.setattr(apply.ct, "review", postcheck)
    return {"root": root, "head": head, "task": task_path, "worker": worker, "reviewer": verifier,
            "sha": sha, "prepared": prepared, "binary": binary, "old": old}


def approve(unit):
    return review.review_proposal(unit["root"], unit["task"], unit["worker"], unit["reviewer"], unit["sha"])


def validate(unit, **kwargs):
    return review.validate_review_receipt(unit["root"], unit["task"], unit["worker"], unit["reviewer"], unit["sha"], **kwargs)


def test_fresh_local_cli_review_is_replayable_and_exact_cache_reuses_no_model(unit, monkeypatch):
    receipt = approve(unit)
    assert receipt["result"]["decision"] == "APPROVE"
    assert receipt["bindings"]["role_config"] == config.role_settings(unit["root"], "final_reviewer")
    assert validate(unit) == receipt
    def forbidden(*args, **kwargs): raise AssertionError("No second provider call allowed")
    monkeypatch.setattr(review.qoder, "qoder_identity", forbidden)
    assert approve(unit) == receipt


def test_only_reviewed_unicode_bytes_are_applied_and_modes_preserved_no_git_commit(unit):
    approve(unit)
    result = apply.apply_reviewed(unit["root"], unit["task"], unit["worker"], unit["reviewer"], unit["sha"])
    assert result["state"] == "APPLIED_BYTES_VERIFIED" and result["git_committed"] is False
    assert result["applied_review"] == "PASSED" and result["next_action"] == "stage_exact_paths_then_commit"
    assert result["review"]["state"] == "BYTES_VALID"
    assert result["changed_paths"] == ["tests/test_old.py", "tests/test_new.py"]
    assert stat.S_IMODE((unit["root"] / "tests/test_old.py").stat().st_mode) == 0o750
    assert stat.S_IMODE((unit["root"] / "tests/test_new.py").stat().st_mode) == 0o644
    assert not list((unit["root"] / "tests").glob(".qoder-apply-*"))
    result_binding = {"gemini_worker_run_id": unit["worker"], "gemini_reviewer_run_id": unit["reviewer"], "proposal_sha256": unit["sha"]}
    assert review.validate_result_review(unit["root"], unit["head"], result_binding)["result"]["decision"] == "APPROVE"


@pytest.mark.parametrize("change", ["config", "runtime", "task", "stream", "capture", "result", "negative"])
def test_missing_stale_negative_or_tampered_approval_never_applies(unit, change):
    receipt = approve(unit); root = unit["root"]
    if change == "config": path = root / config.AI_CONFIG_REL; path.write_bytes(path.read_bytes() + b"\n")
    elif change == "runtime": path = root / config.AI_RUNTIME_REL; path.write_bytes(path.read_bytes() + b"\n")
    elif change == "task": path = root / unit["task"]; path.write_bytes(path.read_bytes() + b"\n")
    elif change == "stream": path = root / receipt["run_dir"] / "stream.jsonl"; path.write_bytes(path.read_bytes() + b"\n")
    elif change == "capture": put(root / receipt["run_dir"] / "capture.json", {"exit_code": 1, "normal_exit_observed": True})
    elif change == "result": put(root / receipt["run_dir"] / "result.json", {"forged": True})
    else:
        path = root / review.REVIEW_REL / (unit["sha"] + ".json")
        data = json.loads(path.read_text()); data["result"].update(decision="REQUEST_CHANGES", findings=["Synthetic finding"]); put(path, data)
    with pytest.raises(apply.ApplyReviewedError):
        apply.apply_reviewed(root, unit["task"], unit["worker"], unit["reviewer"], unit["sha"])
    assert (root / "tests/test_old.py").read_text() == unit["old"]
    assert not (root / "tests/test_new.py").exists()


def test_missing_review_and_forged_task_path_are_not_authority(unit):
    with pytest.raises(apply.ApplyReviewedError):
        apply.apply_reviewed(unit["root"], unit["task"], unit["worker"], unit["reviewer"], unit["sha"])
    receipt = approve(unit); path = unit["root"] / review.REVIEW_REL / (unit["sha"] + ".json")
    receipt["bindings"]["task_path"] = "../../outside.json"; put(path, receipt)
    with pytest.raises(review.QoderReviewError, match="BOUND_TASK_PATH"):
        review.validate_result_review(unit["root"], unit["head"], {"gemini_worker_run_id": unit["worker"],
            "gemini_reviewer_run_id": unit["reviewer"], "proposal_sha256": unit["sha"]})


def test_partial_cross_file_failure_is_explicit_and_keeps_applied_bytes_for_reconciliation(unit, monkeypatch):
    approve(unit); original = apply._publish; calls = []
    def fail_second(*args):
        calls.append(args[1])
        if len(calls) == 2: raise OSError("Synthetic filesystem failure")
        return original(*args)
    monkeypatch.setattr(apply, "_publish", fail_second)
    with pytest.raises(apply.ApplyReviewedError) as caught:
        apply.apply_reviewed(unit["root"], unit["task"], unit["worker"], unit["reviewer"], unit["sha"])
    assert caught.value.applied_paths == ["tests/test_old.py"]
    assert caught.value.attempted_paths == ["tests/test_old.py", "tests/test_new.py"]
    assert caught.value.requires_reconciliation is True
    assert (unit["root"] / "tests/test_old.py").read_text() == unit["prepared"][4][0]["new_content"]
    assert not (unit["root"] / "tests/test_new.py").exists()
    assert not list((unit["root"] / "tests").glob(".qoder-apply-*"))


def test_source_changed_after_preparation_is_not_overwritten(unit):
    approve(unit); target = unit["root"] / "tests/test_old.py"; target.write_text("USER CHANGE\n")
    with pytest.raises(apply.ApplyReviewedError):
        apply.apply_reviewed(unit["root"], unit["task"], unit["worker"], unit["reviewer"], unit["sha"])
    assert target.read_text() == "USER CHANGE\n"


def test_request_changes_is_immutable_and_cannot_authorize_apply(unit):
    bindings = {"proposal_sha256": unit["sha"], "source_head": unit["head"]}
    with pytest.raises(review.QoderReviewError):
        review._decision({"decision": "APPROVE", "findings": ["Unresolved"], **bindings}, bindings)
    assert review._decision({"decision": "REQUEST_CHANGES", "findings": ["Concrete issue"], **bindings}, bindings)["decision"] == "REQUEST_CHANGES"


def test_failed_process_or_nonzero_exit_cannot_publish_approval(unit, monkeypatch):
    def failed_capture(process, *_args, **_kwargs):
        review.runner.terminate_process(process)
    monkeypatch.setattr(review.runner, "capture_bounded_process", failed_capture)
    with pytest.raises(review.QoderReviewError, match="PROCESS_FAILED"):
        approve(unit)
    assert not (unit["root"] / review.REVIEW_REL / (unit["sha"] + ".json")).exists()


def test_final_review_is_wrapped_outside_and_never_nested_inside(tmp_path, monkeypatch):
    from scripts import two_site_coordinator_sandbox as sandbox
    observed = []
    def wrapper(root, run_dir, command, *, environment):
        observed.append((root, run_dir, command, environment))
        return ["synthetic-bwrap", *command]
    monkeypatch.setattr(sandbox, "buildwrap_coordinator_command", wrapper)
    command = ["/synthetic/qodercli", "--print"]
    assert review._contained_command(tmp_path, tmp_path, command, {"SAFE": "1"}) == ["synthetic-bwrap", *command]
    assert len(observed) == 1
    assert review._contained_command(tmp_path, tmp_path, command, {"TWO_SITE_COORDINATOR_SANDBOX": "1"}) == command
    assert len(observed) == 1


@pytest.mark.parametrize("prior", ["worker", "reviewer"])
def test_final_review_cannot_reuse_author_or_gemini_session(unit, prior):
    root, task, worker, reviewer, sha = (unit[k] for k in ("root","task","worker","reviewer","sha"))
    name = worker if prior == "worker" else reviewer
    put(root / ".local/two-site-refactor/proposals" / name / "status.json",
        {"conversation_id": "synthetic-final-session"})
    with pytest.raises(review.QoderReviewError, match="SESSION_NOT_INDEPENDENT"):
        review.review_proposal(root, task, worker, reviewer, sha, timeout=30)
    assert not (root / review.REVIEW_REL / (sha + ".json")).exists()
