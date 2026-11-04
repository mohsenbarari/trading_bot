"""Real disposable process custody; provider/authority layers are not mocked PASS."""
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from scripts import two_site_qoder_author_resume as resume

RUN = "maint-" + "a" * 32 + "-worker-0-qa"
HEAD = "b" * 40


def private(path, value):
    path.write_bytes(value if isinstance(value, bytes) else resume.encode(value)); path.chmod(0o600)


def tool_free_command():
    settings = json.dumps({"general": {"enableAutoUpdate": False}}, separators=(",", ":"))
    return [sys.executable, "-c", "import time; time.sleep(60)", "--", sys.executable,
        "--settings", settings, "--append-system-prompt", "SYNTHETIC",
        "--cwd", "/tmp", "--model", "Synthetic", "--reasoning-effort", "max",
        "--thinking", "adaptive", "--output-format", "stream-json",
        "--no-session-persistence", "--permission-mode", "auto", "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}', "--tools", "", "--print"]


@pytest.fixture
def episode(tmp_path):
    directory = tmp_path / ".local/two-site-refactor/proposals" / RUN
    directory.mkdir(parents=True, mode=0o700)
    task = {"task_id": "synthetic-maintenance", "allowed_files": ["scripts/two_site_example.py"]}
    sources = {"scripts/two_site_example.py": {"content": "PUBLIC_SYNTHETIC", "sha256": "c" * 64}}
    auth = {"kind": resume.MAINTENANCE, "job_id": "maint-" + "a" * 32, "sha256": "d" * 64}
    material = {"task": task, "sources": sources, "source_head": HEAD, "author_authorization": auth}
    status = {"run_id": RUN, "task_id": task["task_id"], "head": HEAD, "role": "worker",
        "state": "RUNNING", "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE", "author_provider": "qoder",
        "completion_policy": "QODER_PROPOSAL_AUTHOR_COMPLETION_V1"}
    private(directory / "task.json", material); private(directory / "status.json", status)
    private(directory / "stream.jsonl", b'{"explicitly_synthetic_partial":')
    private(directory / "stderr.txt", b"")
    process = subprocess.Popen(tool_free_command(),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    values = {"source_head": HEAD, "task_sha256": resume.digest(resume.encode(task)),
              "authorization_sha256": resume.digest(resume.encode(auth))}
    resume.persist_author_process(directory, process, **values,
        command_sha256=resume.digest(resume.encode(process.args)))
    try:
        yield tmp_path, directory, process, values, material
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def stopped(process):
    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)


def prepare(episode):
    root, directory, process, values, material = episode
    return resume.prepare_maintenance_author_recovery(root, directory, **values)


def test_real_live_owner_blocks_before_any_recovery_receipt(episode):
    _, directory, process, _, _ = episode
    owner = json.loads((directory / resume.CUSTODY).read_text())["owner"]
    assert owner["pid"] == owner["pgid"] == process.pid and owner["start_ticks"] > 0
    with pytest.raises(resume.AuthorResumeError, match="PROCESS_LIVE"):
        prepare(episode)
    assert not (directory / resume.RECEIPT).exists()


def test_dead_tool_free_owner_gets_one_idempotent_bound_variant_without_output_adoption(episode):
    root, directory, process, values, material = episode
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    stopped(process)
    receipt = prepare(episode)
    assert receipt == prepare(episode)
    assert receipt["recovery_run_id"] == RUN + "-recovery-1" and receipt["attempt"] == 1
    assert receipt["decision"] == "DISCARD_UNACCEPTED_OUTPUT_NEW_SESSION"
    assert receipt["artifacts"]["stream.jsonl"]["sha256"] == hashlib.sha256(before["stream.jsonl"]).hexdigest()
    assert all((directory / name).read_bytes() == raw for name, raw in before.items())
    assert not (directory.parent / receipt["recovery_run_id"]).exists()
    assert resume.validate_author_recovery(root, receipt, task=material["task"],
        sources=material["sources"], source_head=HEAD, authorization=material["author_authorization"],
        run_id=receipt["recovery_run_id"]) == receipt


@pytest.mark.parametrize("fault", ["extra-tool", "tool-position", "mcp-config", "settings-model",
                                   "thinking-over-output"])
def test_process_custody_rejects_any_unrecognized_or_tool_enabling_argv(episode, fault):
    _, directory, process, values, _ = episode
    stopped(process); (directory / resume.CUSTODY).unlink()
    command = tool_free_command()
    if fault == "extra-tool": command[-1:-1] = ["--allowedTools", "Read"]
    elif fault == "tool-position": command[command.index("--tools") + 1] = "Read"
    elif fault == "mcp-config": command[command.index("--mcp-config") + 1] = '{"mcpServers":{"x":{}}}'
    elif fault == "settings-model":
        command[command.index("--settings") + 1] = json.dumps({"general": {"enableAutoUpdate": False},
            "modelConfigs": {"customOverrides": [{"match": {"model": "Other", "overrideScope": "core"},
                "modelConfig": {"generateContentConfig": {"maxOutputTokens": 262144}}}]}})
    else:
        index = command.index("--thinking")
        command[index:index + 2] = ["--thinking-budget", "32768"]
        command[command.index("--settings") + 1] = json.dumps({
            "general": {"enableAutoUpdate": False}, "modelConfigs": {"customOverrides": [{
                "match": {"model": "Synthetic", "overrideScope": "core"},
                "modelConfig": {"generateContentConfig": {"maxOutputTokens": 32768}}}]}})
    candidate = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        with pytest.raises(resume.AuthorResumeError):
            resume.persist_author_process(directory, candidate, **values,
                command_sha256=resume.digest(resume.encode(command)))
    finally:
        os.killpg(candidate.pid, signal.SIGKILL); candidate.wait(timeout=5)


def test_recovery_variant_has_the_same_process_custody_before_prompt(episode):
    root, directory, process, values, material = episode
    stopped(process); receipt = prepare(episode)
    recovered = directory.parent / receipt["recovery_run_id"]
    recovered.mkdir(mode=0o700); recovered.chmod(0o700)
    recovered_material = copy.deepcopy(material); recovered_material["author_recovery"] = receipt
    status = json.loads((directory / "status.json").read_text()); status["run_id"] = recovered.name
    private(recovered / "task.json", recovered_material); private(recovered / "status.json", status)
    candidate = subprocess.Popen(tool_free_command(), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        custody = resume.persist_author_process(recovered, candidate, **values,
            command_sha256=resume.digest(resume.encode(candidate.args)))
        assert custody["run_id"] == receipt["recovery_run_id"]
    finally:
        os.killpg(candidate.pid, signal.SIGKILL); candidate.wait(timeout=5)


def test_process_custody_accepts_current_bounded_thinking_form(episode):
    _, directory, process, values, _ = episode
    stopped(process); (directory / resume.CUSTODY).unlink()
    command = tool_free_command()
    index = command.index("--thinking")
    command[index:index + 2] = ["--thinking-budget", "32768"]
    candidate = subprocess.Popen(command, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        custody = resume.persist_author_process(directory, candidate, **values,
            command_sha256=resume.digest(resume.encode(command)))
        assert custody["owner"]["pid"] == candidate.pid
    finally:
        os.killpg(candidate.pid, signal.SIGKILL); candidate.wait(timeout=5)


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_process_custody_accepts_bounded_model_retry_control(episode, retries):
    _, directory, process, values, _ = episode
    stopped(process); (directory / resume.CUSTODY).unlink()
    command = tool_free_command()
    index = command.index("--output-format")
    command[index:index] = ["--max-model-request-retries", str(retries)]
    candidate = subprocess.Popen(command, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        custody = resume.persist_author_process(directory, candidate, **values,
            command_sha256=resume.digest(resume.encode(command)))
        assert custody["owner"]["pid"] == candidate.pid
    finally:
        os.killpg(candidate.pid, signal.SIGKILL); candidate.wait(timeout=5)


@pytest.mark.parametrize("retries", [-1, 3, "adaptive"])
def test_process_custody_rejects_unbounded_or_noninteger_model_retry_control(episode, retries):
    _, directory, process, values, _ = episode
    stopped(process); (directory / resume.CUSTODY).unlink()
    command = tool_free_command()
    index = command.index("--output-format")
    command[index:index] = ["--max-model-request-retries", str(retries)]
    candidate = subprocess.Popen(command, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        with pytest.raises(resume.AuthorResumeError, match="MODEL_RETRIES"):
            resume.persist_author_process(directory, candidate, **values,
                command_sha256=resume.digest(resume.encode(command)))
    finally:
        os.killpg(candidate.pid, signal.SIGKILL); candidate.wait(timeout=5)


@pytest.mark.parametrize("artifact", sorted(resume.SUCCESS | {"capture.json"}))
def test_any_completed_or_partly_published_success_requires_reconciliation_not_fresh_generation(episode, artifact):
    _, directory, process, _, _ = episode
    stopped(process); private(directory / artifact, {})
    with pytest.raises(resume.AuthorResumeError, match="RESULT|RECONCILIATION"):
        prepare(episode)


@pytest.mark.parametrize("fault", ["task", "auth", "snapshot-source", "status-head", "business", "missing-custody",
                                   "custody-sha", "hardlink", "symlink", "foreign-file", "second-recovery"])
def test_changed_unknown_linked_or_second_attempt_evidence_fails_closed(episode, fault):
    _, directory, process, values, material = episode
    stopped(process)
    if fault == "task":
        material["task"]["task_id"] += "-drift"; private(directory / "task.json", material)
    elif fault == "auth":
        material["author_authorization"]["sha256"] = "e" * 64; private(directory / "task.json", material)
    elif fault == "snapshot-source":
        material["sources"]["scripts/two_site_example.py"]["content"] += "-drift"; private(directory / "task.json", material)
    elif fault == "status-head":
        status = json.loads((directory / "status.json").read_text()); status["head"] = "e" * 40
        private(directory / "status.json", status)
    elif fault == "business":
        material["author_authorization"]["kind"] = "QODER_BUSINESS_AUTHOR_FALLBACK_V1"
        private(directory / "task.json", material)
    elif fault == "missing-custody":
        (directory / resume.CUSTODY).unlink()
    elif fault == "custody-sha":
        custody = json.loads((directory / resume.CUSTODY).read_text()); custody["task_snapshot_sha256"] = "f" * 64
        private(directory / resume.CUSTODY, custody)
    elif fault == "hardlink":
        os.link(directory / "stream.jsonl", directory.parent / "linked-stream")
    elif fault == "symlink":
        path = directory / "stream.jsonl"; path.unlink(); path.symlink_to(directory / "stderr.txt")
    elif fault == "foreign-file":
        private(directory / "unknown.json", {})
    elif fault == "second-recovery":
        material["author_recovery"] = {}; private(directory / "task.json", material)
    with pytest.raises((resume.AuthorResumeError, OSError)):
        prepare(episode)
    assert not (directory / resume.RECEIPT).exists()


def test_reserved_receipt_or_retained_output_cannot_change_before_replay(episode):
    root, directory, process, _, material = episode
    stopped(process); receipt = prepare(episode)
    private(directory / "stream.jsonl", b'{"different_unaccepted_partial":')
    with pytest.raises(resume.AuthorResumeError, match="RECEIPT"):
        prepare(episode)
    with pytest.raises(resume.AuthorResumeError, match="RECEIPT"):
        resume.validate_author_recovery(root, receipt, task=material["task"], sources=material["sources"],
            source_head=HEAD, authorization=material["author_authorization"], run_id=receipt["recovery_run_id"])


@pytest.mark.parametrize("error", ["QoderError: QODER_NOT_SUCCESS", "ProposalError: authentication failed",
                                   "ProposalError: quota exhausted", "ValueError: malformed output"])
def test_non_interruption_provider_or_implementation_failure_never_grants_retry(episode, error):
    _, directory, process, _, _ = episode
    stopped(process)
    status = json.loads((directory / "status.json").read_text())
    status.update(state="BLOCKED", error=error); private(directory / "status.json", status)
    with pytest.raises(resume.AuthorResumeError, match="NOT_INTERRUPTION"):
        prepare(episode)


def test_retained_capture_interruption_is_eligible_but_typed_receipt_forgery_is_not(episode):
    root, directory, process, _, material = episode
    stopped(process)
    status = json.loads((directory / "status.json").read_text())
    status.update(state="BLOCKED", error="TimeoutExpired: synthetic owned capture deadline")
    private(directory / "status.json", status)
    receipt = prepare(episode)
    forged = copy.deepcopy(receipt); forged["attempt"] = True
    with pytest.raises(resume.AuthorResumeError, match="RECEIPT"):
        resume.validate_author_recovery(root, forged, task=material["task"], sources=material["sources"],
            source_head=HEAD, authorization=material["author_authorization"], run_id=receipt["recovery_run_id"])
    status["quota_evidence"] = {"synthetic": True}; private(directory / "status.json", status)
    with pytest.raises(resume.AuthorResumeError, match="NOT_INTERRUPTION"):
        prepare(episode)


@pytest.mark.parametrize("event", [
    {"type": "result", "subtype": "success", "is_error": False},
    {"type": "result", "subtype": "error_during_execution", "error": "SYNTHETIC_QUOTA"},
    {"type": "error", "code": "SYNTHETIC_AUTH"},
    {"type": "system", "subtype": "error", "message": "SYNTHETIC_QUOTA"},
    {"type": "assistant", "error": {"code": "SYNTHETIC_AUTH"}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write"}]}},
    {"type": "stream_event", "event": {"type": "error", "error": "SYNTHETIC_AUTH"}},
    {"type": "stream_event", "event": {"content_block": {"type": "tool_use", "name": "Write"}}},
])
def test_retained_known_terminal_or_error_is_not_misclassified_as_interruption(episode, event):
    _, directory, process, _, _ = episode
    stopped(process)
    # Even a missing final LF cannot conceal an already parseable terminal.
    private(directory / "stream.jsonl", json.dumps(event).encode())
    with pytest.raises(resume.AuthorResumeError, match="RECONCILIATION|PROVIDER_FAILURE|TOOL_EVENT"):
        prepare(episode)


@pytest.mark.parametrize("body", [b'{"type":"result","type":"system"}\n',
                                  b'{"type":"system","value":NaN}\n',
                                  b'not-a-complete-sdk-record\n', b'{"foreign":true}\n'])
def test_malformed_duplicate_nonfinite_or_unknown_complete_sdk_record_blocks(episode, body):
    _, directory, process, _, _ = episode
    stopped(process); private(directory / "stream.jsonl", body)
    with pytest.raises(resume.AuthorResumeError):
        prepare(episode)


def test_stderr_failure_without_sdk_terminal_cannot_retry_as_empty_interruption(episode):
    _, directory, process, _, _ = episode
    stopped(process); private(directory / "stderr.txt", b"SYNTHETIC_AUTH_OR_QUOTA_DIAGNOSTIC")
    with pytest.raises(resume.AuthorResumeError, match="DIAGNOSTIC_REQUIRES_RECONCILIATION"):
        prepare(episode)


def test_living_descendant_group_blocks_even_after_own_parent_has_exited(episode, tmp_path):
    _, directory, initial, _, _ = episode
    stopped(initial)
    owner_file = directory / resume.CUSTODY; owner_file.unlink()
    child_file = tmp_path / "synthetic-child.pid"
    code = ("import subprocess,sys,pathlib,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
            "pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(.2)")
    process = subprocess.Popen([sys.executable, "-c", code, str(child_file)], start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        owner = resume._process(process.pid)
        assert owner["pgid"] == process.pid
        process.wait(timeout=5)
        assert resume._process(process.pid) is None
        with pytest.raises(resume.AuthorResumeError, match="GROUP_LIVE"):
            resume._gone(owner)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


@pytest.mark.parametrize("event", [
    {"type": "assistant", "message": {"stop_reason": "max_tokens", "content": [{"type": "thinking", "thinking": "SYNTHETIC"}]}},
    {"type": "assistant", "stop_reason": "max_tokens"},
    {"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}},
    {"type": "stream_event", "event": {"stop_reason": "max_tokens"}},
])
def test_known_output_budget_exhaustion_never_receives_same_budget_interruption_retry(episode, event):
    _, directory, process, _, _ = episode
    stopped(process)
    private(directory / "stream.jsonl", json.dumps(event).encode())
    with pytest.raises(resume.AuthorResumeError, match="OUTPUT_BUDGET_REQUIRES_RECONCILIATION"):
        prepare(episode)
    assert not (directory / resume.RECEIPT).exists()
