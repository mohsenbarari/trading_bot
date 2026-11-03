"""Ordinary Reviewer continuation: real saved proofs, synthetic Provider only."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import two_site_proposal_pipeline as pipeline
from scripts import two_site_proposal_runner as runner
from scripts.two_site_semantic_attempts import MARKER
from scripts.two_site_semantic_task import build_identity
from tests.test_two_site_proposal_pipeline import HEAD, fake_git, make_runner, passed_receipt


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    source = root / "src.py"
    source.write_text("# original\n")
    sources = lambda: {"src.py": {"sha256": runner.file_sha256(source), "content": source.read_text()}}
    identity = build_identity(
        stage_id="P1-00b", gap_reference="docs/refactor/two-server/PROPOSAL_AUTOMATION.md#resume",
        scenario_key="reviewer_phase_resume", symbol_groups=[],
        source_manifest={"src.py": runner.file_sha256(source)},
    )
    task = {
        "task_id": "RESUME1", "goal": "One real outcome" + MARKER + json.dumps({
            "head": HEAD, "gap_reference": identity["gap_reference"], "excerpts": [],
            "semantic_identity": identity,
        }), "context_files": ["src.py"], "allowed_files": ["src.py"],
        "requirements": ["R1"], "test_selectors": ["tests/test_a.py"],
    }
    task_path = root / "task.json"
    runner.write_json(task_path, task)
    monkeypatch.delenv("TWO_SITE_AUTOPILOT_RUN_ID", raising=False)
    monkeypatch.setattr(pipeline, "git", fake_git)
    monkeypatch.setattr(pipeline, "load_task", lambda *_a: (copy.deepcopy(task), sources()))
    # This reader may already be imported by quota tests during collection.
    # Bind its task loader explicitly instead of depending on lazy import order.
    from scripts import two_site_quota_resume_worker_reader as worker_reader
    monkeypatch.setattr(worker_reader, "load_task", pipeline.load_task)
    tests = []

    def native(*args, **_kwargs):
        tests.append(args[3])
        return passed_receipt(args[3])

    monkeypatch.setattr(pipeline, "run_candidate_tests", native)
    state = {
        "state": "TASK_BLOCKED", "task_id": "", "source_head": "",
        "final_worker_run_id": None, "final_reviewer_run_id": None,
        "proposal_sha256": None, "changed_paths": [], "proposal_path": None,
        "reviewer_path": None, "correction_count": 0, "blockers": [],
        "pre_review_test_path": None, "pre_review_test_sha256": None,
        "pre_review_test_state": None, "semantic_task_fingerprint": None,
    }
    return root, task_path, task, sources, state, tests


def failed_reviewer(root, task, sources, run_id, proposal, test_evidence, *, profile=None, mutation=None,
                    framed=False, zero_token=False):
    """Create an explicit synthetic failed CLI run, never a successful proof."""
    out = root / ".local/two-site-refactor/proposals" / run_id
    out.mkdir(mode=0o700, parents=True)
    worker = json.loads((proposal.parent / "status.json").read_text())
    receipt = json.loads(test_evidence.read_text())
    model_config = runner.configured_gemini_roles(root)
    model = model_config["reviewer"]["model"]
    status = {
        "run_id": run_id, "task_id": task["task_id"], "role": "verifier",
        "head": HEAD, "model": model, "state": "BLOCKED",
        "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE",
        "model_config": model_config,
        "model_config_sha256": runner.sha(runner.serialize_json(model_config)),
        "input_worker_run_id": worker["run_id"],
        "input_worker_conversation_id": worker["conversation_id"],
        "input_proposal_sha256": worker["result_sha256"],
        "input_test_evidence_sha256": runner.file_sha256(test_evidence),
        "error": "RetryableProviderSessionError: provider stream interrupted before a complete result",
    }
    snapshot = {"source_head": HEAD, "task": copy.deepcopy(task), "sources": sources(),
                "proposal": proposal.read_text(), "test_evidence": receipt}
    prompt = "synthetic public prompt\n"
    if profile:
        status["error"] = "ProposalProfileError: " + profile
    else:
        cid = "failed-" + run_id
        stream = [
            {"event": "init", "conversation_id": cid, "init": {"model": model}},
            {"event": "result", "result": {"status": "ERROR", "conversation_id": cid,
                "error": "stream interrupted", "response": "partial NOT adopted"}},
        ]
        if zero_token:
            status["error"] = "RetryableProviderSessionError: CLI did not complete one successful session"
            stream[-1]["result"].update(error="Agent execution terminated due to error.", response="",
                                         usage={"input_tokens": 0, "output_tokens": 0})
        if framed:
            prompt = "synthetic public input " * 9000
            manifest, _wires = runner._framed_input_plan(prompt)
            runner.write_json(out / "input-framing.json", manifest)
            status.update(input_transport="FULL_INPUT_ACK_V1",
                          input_framing_sha256=runner.file_sha256(out / "input-framing.json"))
            status["error"] = status["error"].replace("RetryableProviderSessionError:", "ProposalError:", 1)
            terminal = stream.pop()
            for frame in manifest["frames"][:-1]:
                stream.append({"event": "result", "result": {
                    "status": "SUCCESS", "conversation_id": cid, "num_turns": frame["index"],
                    "structured_output": frame["ack"],
                }})
            terminal["result"]["num_turns"] = manifest["frame_count"]
            stream.append(terminal)
        (out / "stream.jsonl").write_text("\n".join(json.dumps(e)for e in stream)+"\n")
        (out / "stderr.txt").write_bytes(b"")
    (out / "prompt.txt").write_text(prompt)
    runner.write_json(out / "response-schema.json", {"type": "object"})
    if mutation:
        mutation(out, status, snapshot)
    runner.write_json(out / "task.json", snapshot)
    runner.write_json(out / "status.json", status)
    if profile:
        raise pipeline.ProposalProfileError(profile)
    raise pipeline.RetryableProviderSessionError(
        "CLI did not complete one successful session" if zero_token else "provider stream interrupted before a complete result")


def install_provider(scenario, monkeypatch, *, fail_count=3, profile=None, mutation=None, responses=None,
                     correction_only=False, framed=False, zero_token=False):
    root, _path, task, sources, _state, _tests = scenario
    success, _ = make_runner(root, responses or [{"content": "# proposed\n"}, {"status": "VERIFIED"}])
    calls = []
    failures = []

    def run(_root, path, role, run_id, timeout, **kwargs):
        calls.append((role, run_id))
        # The tests can target a correction's Reviewer without changing Worker behavior.
        targeted = role == "verifier" and (not correction_only or "-reviewer-1" in run_id or "-v1r" in run_id)
        if targeted and len(failures) < fail_count:
            failures.append(run_id)
            failed_reviewer(root, task, sources, run_id, kwargs["proposal"], kwargs["test_evidence"],
                            profile=profile, mutation=mutation, framed=framed, zero_token=zero_token)
        return success(_root, path, role, run_id, timeout, **kwargs)

    monkeypatch.setattr(pipeline, "runner_run", run)
    return calls, failures


def test_late_reviewer_transport_resumes_same_worker_and_passed_native(scenario, monkeypatch):
    root, path, _task, _sources, state, tests = scenario
    calls, failed = install_provider(scenario, monkeypatch)
    result = pipeline.run_pipeline(root, path, "run-resume", 60, None, state)
    assert result["state"] == "READY_FOR_CODEX"
    assert [r for role, r in calls if role == "worker"] == ["run-resume-worker-0"]
    assert len(tests) == 1 and len(failed) == 3
    assert result["final_reviewer_run_id"] == "run-resume-v0r1"
    receipt = json.loads((root / ".local/two-site-refactor/pipeline/run-resume/reviewer-phase-0-batch-0.json").read_text())
    assert receipt["action"] == "CONTINUE_SAME_REVIEWER_PHASE"
    assert receipt["resume_batches_used"] == 0 and receipt["remaining_resume_batches"] == 2
    assert [item["run_id"]for item in receipt["failed_attempts"]] == failed
    for item in receipt["failed_attempts"]:
        old = root / ".local/two-site-refactor/proposals" / item["run_id"]
        assert runner.file_sha256(old / "status.json") == item["status_sha256"]
        assert runner.file_sha256(old / "task.json") == item["task_snapshot_sha256"]
        assert runner.file_sha256(old / "stream.jsonl") == item["stream_sha256"]
        assert not (old / "result.json").exists()
    # Both the immutable semantic reservation and the original Worker remain the owners.
    semantic = pipeline.read_existing_semantic_attempt(root, root / ".local/two-site-refactor/pipeline",
                                                     _task, "run-resume")
    assert semantic["fingerprint"] == state["semantic_task_fingerprint"]


def test_two_continuation_batches_exhaust_without_reset_or_worker_replay(scenario, monkeypatch):
    root, path, _task, _sources, state, tests = scenario
    calls, failed = install_provider(scenario, monkeypatch, fail_count=99)
    with pytest.raises(pipeline.ReviewerPhaseExhausted) as error:
        pipeline.run_pipeline(root, path, "run-exhaust", 60, None, state)
    assert len(failed) == 9 and len(tests) == 1
    assert sum(role == "worker"for role, _ in calls) == 1
    assert error.value.phase_failure["resume_batches_used"] == 2
    assert error.value.phase_failure["remaining_resume_batches"] == 0
    assert error.value.phase_failure["action"] == "HALT_BUDGET_EXHAUSTED"
    old_calls = list(calls)
    with pytest.raises(pipeline.ProposalError, match="already exists"):
        pipeline.run_pipeline(root, path, "run-exhaust", 60, None, state)
    assert calls == old_calls


@pytest.mark.parametrize("kind", ["source", "head", "task", "proposal", "native", "accepted", "stop", "root_stop"])
def test_stale_or_accepted_phase_never_starts_extra_provider(scenario, monkeypatch, kind):
    root, path, _task, _sources, state, _tests = scenario
    count = []

    def mutate(_out, _status, _snapshot):
        count.append(1)
        if len(count) != 3:
            return
        if kind == "source":
            (root / "src.py").write_text("# drift\n")
        elif kind == "head":
            monkeypatch.setattr(pipeline, "git", lambda r, *a: "c"*40 if a == ("rev-parse", "HEAD") else fake_git(r, *a))
        elif kind == "task":
            _task["requirements"] = ["changed after dispatch"]
        elif kind == "proposal":
            (root / state["proposal_path"]).write_text("{}")
        elif kind == "native":
            (root / state["pre_review_test_path"]).write_text("{}")
        elif kind == "accepted":
            state["state"] = "READY_FOR_CODEX"
        else:
            stop = root / "STOP" if kind == "root_stop" else root / ".local/two-site-refactor/autopilot/STOP"
            stop.parent.mkdir(parents=True, exist_ok=True)
            stop.write_text("user paused\n")

    calls, failures = install_provider(scenario, monkeypatch, mutation=mutate)
    with pytest.raises(pipeline.ProposalError):
        pipeline.run_pipeline(root, path, "run-stale", 60, None, state)
    assert len(failures) == 3 and len(calls) == 4


@pytest.mark.parametrize("kind", ["result", "binding", "cleanup", "config", "semantic_output"])
def test_unsafe_or_nontransient_failure_is_not_recoverable(scenario, monkeypatch, kind):
    root, path, _task, _sources, state, _tests = scenario

    def mutate(out, status, _snapshot):
        if kind == "result":
            (out / "result.json").write_text("{}")
        elif kind == "binding":
            status["input_proposal_sha256"] = "0"*64
        elif kind == "cleanup":
            status["secondary_context_error_type"] = "RuntimeError"
        elif kind == "config":
            status["model_config_sha256"] = "0"*64
        else:
            status["error"] = "RetryableProviderOutputError: incomplete response contract"

    calls, failures = install_provider(scenario, monkeypatch, mutation=mutate)
    with pytest.raises(pipeline.ProposalError) as error:
        pipeline.run_pipeline(root, path, "run-unsafe", 60, None, state)
    assert not isinstance(error.value, pipeline.ReviewerPhaseExhausted)
    assert len(failures) == 3 and len(calls) == 4
    assert not list((root / ".local/two-site-refactor/pipeline/run-unsafe").glob("reviewer-phase-*.json"))


@pytest.mark.parametrize("profile", [pipeline._EMPTY_INITIAL_PROFILE, pipeline._EMPTY_INITIAL_PROFILE_PRE_SECOND])
def test_exact_empty_preparation_signature_can_use_fresh_phase_batch(scenario, monkeypatch, profile):
    root, path, _task, _sources, state, tests = scenario
    calls, failed = install_provider(scenario, monkeypatch, profile=profile)
    assert pipeline.run_pipeline(root, path, "run-profile", 60, None, state)["state"] == "READY_FOR_CODEX"
    assert len(failed) == 3 and len(tests) == 1 and len(calls) == 5


def test_correction_count_and_prior_semantic_findings_survive_reviewer_resume(scenario, monkeypatch):
    root, path, _task, _sources, state, tests = scenario
    calls, failed = install_provider(scenario, monkeypatch, correction_only=True, responses=[
        {"content": "# first\n"}, {"status": "CHANGES_REQUIRED", "findings": ["assert the missing exact field"]},
        {"content": "# corrected\n"}, {"status": "VERIFIED"},
    ])
    result = pipeline.run_pipeline(root, path, "run-correction", 60, None, state)
    assert result["state"] == "READY_FOR_CODEX" and result["correction_count"] == 1
    assert result["final_worker_run_id"] == "run-correction-worker-1"
    assert result["final_reviewer_run_id"] == "run-correction-v1r1"
    assert sum(role == "worker"for role, _ in calls) == 2
    assert len(failed) == 3 and len(tests) == 2


def test_successful_reviewer_cannot_reuse_a_failed_conversation(scenario, monkeypatch):
    root, path, _task, _sources, state, tests = scenario
    calls, failed = install_provider(scenario, monkeypatch, responses=[
        {"content": "# proposed\n"},
        {"status": "VERIFIED", "conv_id": "failed-run-reused-reviewer-0"},
    ])
    with pytest.raises(pipeline.ProposalError, match="conversation"):
        pipeline.run_pipeline(root, path, "run-reused", 60, None, state)
    assert len(failed) == 3 and len(tests) == 1
    assert sum(role == "worker" for role, _ in calls) == 1


@pytest.mark.parametrize("reuse", ["within_batch", "prior_worker"])
def test_failed_conversation_reuse_never_grants_a_fresh_batch(scenario, monkeypatch, reuse):
    root, path, _task, _sources, state, tests = scenario
    prefix = "run-failed-cid"
    cid = "same-failed-conversation" if reuse == "within_batch" else f"conv-{prefix}-worker-0"

    def mutate(out, _status, _snapshot):
        events = [json.loads(line) for line in (out / "stream.jsonl").read_text().splitlines()]
        for event in events:
            event["conversation_id"] = cid
            if event["event"] == "result":
                event["result"]["conversation_id"] = cid
        (out / "stream.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")

    calls, failed = install_provider(scenario, monkeypatch, mutation=mutate)
    with pytest.raises(pipeline.ProposalError):
        pipeline.run_pipeline(root, path, prefix, 60, None, state)
    assert len(failed) == 3 and len(calls) == 4 and len(tests) == 1
    assert not list((root / ".local/two-site-refactor/pipeline" / prefix).glob("reviewer-phase-*.json"))


@pytest.mark.parametrize("changed", ["stream", "snapshot", "success_artifact", "new_pregen_stream"])
def test_retained_failed_evidence_cannot_change_before_next_batch(scenario, monkeypatch, changed):
    root, path, _task, _sources, state, tests = scenario
    profile = pipeline._EMPTY_INITIAL_PROFILE if changed == "new_pregen_stream" else None
    calls, failed = install_provider(scenario, monkeypatch, profile=profile)
    classify = pipeline._reviewer_batch_failure

    def classify_then_change(*args, **kwargs):
        failures = classify(*args, **kwargs)
        assert failures is not None
        directory = root / ".local/two-site-refactor/proposals" / failures[0]["run_id"]
        name = {"stream": "stream.jsonl", "snapshot": "task.json", "success_artifact": "result.json",
                "new_pregen_stream": "stream.jsonl"}[changed]
        target = directory / name
        target.write_bytes(target.read_bytes() + b"\n" if target.exists() else b"{}\n")
        return failures

    monkeypatch.setattr(pipeline, "_reviewer_batch_failure", classify_then_change)
    with pytest.raises(pipeline.ProposalError, match="before continuation"):
        pipeline.run_pipeline(root, path, "run-retained", 60, None, state)
    assert len(failed) == 3 and len(calls) == 4 and len(tests) == 1


@pytest.mark.parametrize("zero_token", [False, True])
def test_framed_failure_uses_existing_exact_ack_and_terminal_classifier(scenario, monkeypatch, zero_token):
    root, path, _task, _sources, state, tests = scenario
    calls, failed = install_provider(scenario, monkeypatch, framed=True, zero_token=zero_token)
    if zero_token:
        # The legacy framed parser does not classify this generic ERROR as transient.
        # A forged retryable exception cannot expand that original classification.
        with pytest.raises(pipeline.ProposalError):
            pipeline.run_pipeline(root, path, "run-framed", 60, None, state)
        assert len(failed) == 3 and len(calls) == 4
        return
    result = pipeline.run_pipeline(root, path, "run-framed", 60, None, state)
    assert result["state"] == "READY_FOR_CODEX" and len(failed) == 3 and len(tests) == 1
    assert sum(role == "worker" for role, _ in calls) == 1
    receipt = json.loads((root / ".local/two-site-refactor/pipeline/run-framed/reviewer-phase-0-batch-0.json").read_text())
    for item in receipt["failed_attempts"]:
        assert item["input_framing_sha256"]


def test_forged_framed_ack_does_not_grant_continuation(scenario, monkeypatch):
    root, path, _task, _sources, state, _tests = scenario

    def mutate(out, _status, _snapshot):
        events = [json.loads(line) for line in (out / "stream.jsonl").read_text().splitlines()]
        events[1]["result"]["structured_output"]["summary"] = "fake ACK"
        (out / "stream.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")

    calls, failed = install_provider(scenario, monkeypatch, framed=True, mutation=mutate)
    with pytest.raises(pipeline.ProposalError):
        pipeline.run_pipeline(root, path, "run-bad-ack", 60, None, state)
    assert len(failed) == 3 and len(calls) == 4


def final_hook(decisions, calls, *, mismatch=False):
    """Persist explicit synthetic final-review receipts without invoking any CLI."""
    def review(root, task_path, worker, reviewer, proposal_sha, *, timeout):
        native = json.loads((root / ".local/two-site-refactor/proposals" / reviewer / "task.json").read_text())["test_evidence"]
        assert native["state"] == "PASSED" and native["proposal_sha256"] == proposal_sha
        assert json.loads((root / ".local/two-site-refactor/proposals" / reviewer / "result.json").read_text())["status"] == "VERIFIED"
        calls.append((worker, reviewer, proposal_sha))
        decision = decisions[len(calls) - 1]
        receipt = {"result": {"decision": decision, "findings": [] if decision == "APPROVE" else ["assert exact original actor value"],
                    "source_head": HEAD, "proposal_sha256": "0"*64 if mismatch else proposal_sha}}
        path = root / ".local/two-site-refactor/qoder-reviews" / (proposal_sha + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        runner.write_json(path, receipt)
        return receipt
    return review


def test_final_qoder_findings_enter_same_bounded_correction_after_verified_gemini(scenario, monkeypatch):
    root, path, _task, _sources, state, tests = scenario
    provider, _failed = install_provider(scenario, monkeypatch, fail_count=0, responses=[
        {"content": "# first\n"}, {"status": "VERIFIED"},
        {"content": "# fixed exact actor\n"}, {"status": "VERIFIED"},
    ])
    final_calls = []
    result = pipeline.run_pipeline(root, path, "run-final", 60, None, state,
        final_review_hook=final_hook(["REQUEST_CHANGES", "APPROVE"], final_calls))
    assert result["state"] == "READY_FOR_CODEX" and result["correction_count"] == 1
    assert len(tests) == 2 and len(provider) == 4 and len(final_calls) == 2
    directory = root / ".local/two-site-refactor/pipeline/run-final"
    feedback = json.loads((directory / "feedback-correction-1.json").read_text())
    assert feedback["reviewer"] == "qoder" and feedback["reviewed_proposal_sha256"] == final_calls[0][2]
    for index, decision in enumerate(("REQUEST_CHANGES", "APPROVE")):
        history = json.loads((directory / f"qoder-review-{index}.json").read_text())
        assert history["decision"] == decision
        assert runner.file_sha256(root / history["qoder_review_receipt"]) == history["qoder_review_receipt_sha256"]
        assert json.loads((root / ".local/two-site-refactor/proposals" / history["gemini_reviewer_run_id"] / "result.json").read_text())["status"] == "VERIFIED"


def test_final_qoder_mismatched_proposal_is_never_ready_or_corrected(scenario, monkeypatch):
    root, path, _task, _sources, state, tests = scenario
    calls, _ = install_provider(scenario, monkeypatch, fail_count=0)
    with pytest.raises(pipeline.ProposalError, match="proposal binding"):
        pipeline.run_pipeline(root, path, "run-final-bad", 60, None, state,
            final_review_hook=final_hook(["APPROVE"], [], mismatch=True))
    assert state["state"] != "READY_FOR_CODEX" and len(calls) == 2 and len(tests) == 1


def test_final_qoder_recurring_defect_does_not_reset_correction_history(scenario, monkeypatch):
    root, path, _task, _sources, state, tests = scenario
    calls, _ = install_provider(scenario, monkeypatch, fail_count=0, responses=[
        {"content": "# first\n"}, {"status": "VERIFIED"},
        {"content": "# still missing exact assertion\n"}, {"status": "VERIFIED"},
    ])
    with pytest.raises(pipeline.ProposalError, match="recurring normalized findings"):
        pipeline.run_pipeline(root, path, "run-final-repeat", 60, None, state,
            final_review_hook=final_hook(["REQUEST_CHANGES", "REQUEST_CHANGES"], []))
    assert state["correction_count"] == 1 and len(calls) == 4 and len(tests) == 2
