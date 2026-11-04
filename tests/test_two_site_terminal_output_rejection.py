"""Hermetic rejection tests: synthetic Python children are not provider proof."""
from __future__ import annotations

from contextlib import contextmanager
import ast
import inspect
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from scripts import two_site_generation_completion as completion
from scripts import two_site_proposal_runner as runner
from tests import two_site_runner_752_fixture as seventh


PATH = "tests/test_owned_terminal_budget.py"
CID = "00000000-0000-4000-8000-000000000797"
INPUT = b"owned synthetic request\n"
CAP = 64
VALID = "def test_owned():\n    assert True\n"
INVALID = "#" + "x" * 100 + "\n"
MESSAGE = "new test file content exceeds budget: " + PATH


def _task():
    budget = {"version": "1.0", "file_limits": {PATH: CAP}, "total_limit": CAP}
    return {
        "task_id": "TEST-001", "goal": "Exercise one owned synthetic output.",
        "context_files": [], "allowed_files": [PATH], "test_selectors": [PATH],
        "requirements": [
            "Return one bounded test.",
            "PROPOSAL_UTF8_BUDGET_V1:" + json.dumps(
                budget, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
        ],
    }


def _sources():
    return {PATH: {"sha256": None, "content": None}}


def _wire(content, *, cid=CID, model=None, tool=False):
    result = {
        "status": "PROPOSAL_READY", "summary": "Synthetic bounded test.",
        "covered_requirements": [1, 2], "findings": [],
        "changes": [{"path": PATH, "original_sha256": None,
                     "content": content, "replacements": []}],
    }
    frames = [{"event": "init", "conversation_id": cid,
               "init": {"model": runner.WORKER_MODEL if model is None else model}}]
    if tool:
        frames.append({"event": "step_update", "conversation_id": cid,
                       "step_update": {"step_type": "tool", "tool_name": "read_file"}})
    frames.append({"event": "result", "result": {
        "status": "SUCCESS", "conversation_id": cid, "num_turns": 1,
        "response": json.dumps(result),
    }})
    return b"".join(json.dumps(frame).encode() + b"\n" for frame in frames)


def _check(stdout, stderr=b"", *, role="worker", original_sources=None):
    return runner._generation_final_frame_check(
        stdout, stderr, _task(), _sources(), role, (), runner.WORKER_MODEL, CID,
        original_sources=original_sources,
    )


@contextmanager
def _child(tmp_path, output, *, hang=True, read_input=True):
    home = tmp_path / "empty-home"
    home.mkdir(exist_ok=True)
    body = ("import os,sys,time\n"
            + ("sys.stdin.buffer.read()\n" if read_input else "os.close(0)\n")
            + f"os.write(1, {output!r})\n"
            + ("time.sleep(60)\n" if hang else "sys.exit(0)\n"))
    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", body], cwd=tmp_path,
        env={"PATH": os.defpath, "HOME": str(home), "LANG": "C", "LC_ALL": "C"},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True, close_fds=True,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def _capture(process, tmp_path, policy, *, data=INPUT):
    return completion.capture_generation_process(
        process, data, tmp_path / "stdout.bin", tmp_path / "stderr.bin",
        20.0, 65536, policy, error_type=runner.ProposalError,
        terminate=runner.terminate_process, stream_chunk_size=13,
    )


def _assert_rejected_cleanup(process, tmp_path, policy):
    assert process.poll() is not None
    assert all(stream.closed for stream in
               (process.stdin, process.stdout, process.stderr))
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)
    facts = policy.failure_facts
    assert facts["kind"] == "GENERATION_CAPTURE_FAILURE_OBSERVATIONS_V1"
    assert facts["eligible"] is False
    assert facts["before_cleanup"]["signal_request_attempted"] is False
    assert facts["before_cleanup"]["capture_deadline_reached"] is False
    assert facts["after_cleanup"]["parent_poll_returncode"] == process.returncode
    assert not any((tmp_path / name).exists() for name in
                   ("result.json", "proposal.patch", "transport-completion.json"))


def test_real_hanging_terminal_budget_error_is_prompt_original_error_and_cleaned(tmp_path):
    policy = completion.GenerationRecoveryPolicy(_check)
    actual_validate = runner.validate_result
    raised = []

    def observe_validate(*args, **kwargs):
        try:
            return actual_validate(*args, **kwargs)
        except runner.ProposalError as error:
            raised.append(error)
            raise

    with _child(tmp_path, _wire(INVALID)) as process:
        started = time.monotonic()
        with patch.object(runner, "validate_result", side_effect=observe_validate):
            with pytest.raises(runner.ProposalError) as caught:
                _capture(process, tmp_path, policy)
        assert time.monotonic() - started < 10.0
        assert type(caught.value) is runner.ProposalError
        assert str(caught.value) == MESSAGE
        assert raised == [caught.value] and raised[0] is caught.value
        assert process.returncode == -signal.SIGKILL
        _assert_rejected_cleanup(process, tmp_path, policy)
        assert policy.failure_facts["before_cleanup"]["terminal_read_observed"] is True
        assert policy.failure_facts["before_cleanup"]["input_writer_close_observed"] is True
        assert policy.failure_facts["after_cleanup"]["owned_group_absent"] is True
    assert (tmp_path / "stdout.bin").read_bytes() == _wire(INVALID)
    assert (tmp_path / "stderr.bin").read_bytes() == b""


def test_naturally_exited_invalid_terminal_keeps_same_budget_rejection(tmp_path):
    policy = completion.GenerationRecoveryPolicy(_check)
    with _child(tmp_path, _wire(INVALID), hang=False, read_input=False) as process:
        assert process.wait(timeout=3) == 0
        with pytest.raises(runner.ProposalError) as caught:
            _capture(process, tmp_path, policy, data=b"")
        assert type(caught.value) is runner.ProposalError
        assert str(caught.value) == MESSAGE
        assert process.returncode == 0
        _assert_rejected_cleanup(process, tmp_path, policy)
    assert (tmp_path / "stdout.bin").read_bytes() == _wire(INVALID)


def test_valid_hanging_final_keeps_real_five_second_recovery_and_eofs(tmp_path):
    policy = completion.GenerationRecoveryPolicy(_check)
    with _child(tmp_path, _wire(VALID)) as process:
        started = time.monotonic()
        result = _capture(process, tmp_path, policy)
        assert time.monotonic() - started >= 5.0
        assert completion.POST_FINAL_GRACE_SECONDS == 5.0
        assert process.returncode == -signal.SIGKILL
        assert all(stream.closed for stream in
                   (process.stdin, process.stdout, process.stderr))
        with pytest.raises(ProcessLookupError):
            os.killpg(process.pid, 0)
    stdout, stderr = (tmp_path / "stdout.bin").read_bytes(), (tmp_path / "stderr.bin").read_bytes()
    assert (stdout, stderr) == (_wire(VALID), b"")
    assert _check(stdout, stderr) is True
    checked = completion.validate_observations(result, INPUT, stdout, stderr)
    assert checked["outcome"] == "RECOVERED_COMPLETED_RESULT"
    assert checked["normal_exit_observed"] is False
    for name in ("stdout_eof", "stderr_eof", "direct_child_reaped",
                 "owned_group_absent", "full_input_written", "input_writer_closed",
                 "observed_close_before_terminal_read", "lifecycle_within_capture_deadline"):
        assert checked[name] is True
    assert policy.failure_facts is None


def test_protocol_identity_tools_partial_stderr_and_unknown_failures_stay_closed():
    for raw, stderr in (
        (_wire(INVALID, cid="unowned-session"), b""),
        (_wire(INVALID, model="wrong-model"), b""),
        (_wire(INVALID, tool=True), b""),
        (_wire(INVALID)[:-1], b""),
        (_wire(INVALID) + b'{"event":', b""),
        (_wire(INVALID), b"unexpected stderr"),
        (b"\xff\n", b""),
    ):
        assert _check(raw, stderr) is False
    unknown = RuntimeError("sentinel")
    with patch.object(runner, "_validate_final_generation_frame", side_effect=unknown):
        with pytest.raises(RuntimeError) as caught:
            _check(_wire(INVALID))
    assert caught.value is unknown


def test_unmarked_forged_subclass_role_path_and_original_bindings_cannot_fast_reject():
    with pytest.raises(runner.ProposalError) as caught:
        _check(_wire(INVALID))
    trusted = caught.value
    marker = trusted.__dict__["_terminal_new_test_budget"]

    class DifferentError(runner.ProposalError):
        pass

    failures = [runner.ProposalError(MESSAGE), DifferentError(MESSAGE)]
    failures[1]._terminal_new_test_budget = marker
    for evidence, name in (
        ((object(), PATH, CAP, len(INVALID.encode())), PATH),
        ((marker[0], "tests/test_unknown.py", CAP, len(INVALID.encode())),
         "tests/test_unknown.py"),
        ((marker[0], PATH, True, len(INVALID.encode())), PATH),
        ((marker[0], PATH, CAP, True), PATH),
        ((marker[0], PATH, CAP, CAP), PATH),
        ((marker[0], PATH, CAP + 1, len(INVALID.encode())), PATH),
    ):
        error = runner.ProposalError("new test file content exceeds budget: " + name)
        error._terminal_new_test_budget = evidence
        failures.append(error)
    for error in failures:
        with patch.object(runner, "_validate_final_generation_frame", side_effect=error):
            assert _check(_wire(INVALID)) is False
    existing = {PATH: {"sha256": runner.sha(b"old\n"), "content": "old\n"}}
    with patch.object(runner, "_validate_final_generation_frame", side_effect=trusted):
        assert _check(_wire(INVALID), role="verifier") is False
        assert _check(_wire(INVALID), original_sources=existing) is False
        assert _check(_wire(INVALID), original_sources={}) is False


def test_early_terminal_before_full_input_cannot_call_checker_or_gain_eligibility(tmp_path):
    checks = []

    def observe(stdout, stderr):
        checks.append(True)
        return _check(stdout, stderr)

    policy = completion.GenerationRecoveryPolicy(observe)
    with _child(tmp_path, _wire(INVALID), hang=False, read_input=False) as process:
        with pytest.raises(runner.ProposalError, match="input was not completely written"):
            _capture(process, tmp_path, policy, data=b"x" * (2 * 1024 * 1024))
        assert checks == []
        _assert_rejected_cleanup(process, tmp_path, policy)
        assert policy.failure_facts["before_cleanup"]["input_writer_close_observed"] is False


def test_default_capture_is_unchanged_and_policy_selection_remains_gemini_only(tmp_path):
    with patch.object(runner, "_generation_final_frame_check",
                      side_effect=AssertionError("default capture called checker")) as check:
        with _child(tmp_path, b"ordinary default output\n", hang=False) as process:
            assert runner.capture_bounded_process(
                process, INPUT, tmp_path / "stdout.bin", tmp_path / "stderr.bin", 3.0,
            ) is None
            assert process.returncode == 0
        check.assert_not_called()
    assert (tmp_path / "stdout.bin").read_bytes() == b"ordinary default output\n"
    body = inspect.getsource(runner.run)
    assert "if input_framing is None:" in body
    assert "generation_policy = GenerationRecoveryPolicy(final_frame_check)" in body
    assert runner._provider_output_is_retryable(runner.ProposalError(MESSAGE)) is False


def test_runner_publication_and_disposable_exit_stay_blocked_on_original_budget_error():
    # Reuse the existing offline CLI fixture, not provider/runtime acceptance.
    from tests.test_two_site_proposal_runner import ProposalContracts

    fixture = ProposalContracts(methodName="test_valid_new_and_existing_file")
    fixture.setUp()
    try:
        fixture.task = dict(_task(), context_files=["source.py"])
        # Fresh roles use the real shared model policy, not historical routing.
        fixture.task["task_id"] = "TERMINAL-BUDGET-CONFIGURED"
        assert runner.configured_gemini_role(fixture.root, "worker")["model"] == runner.WORKER_MODEL
        fixture.save_task()
        _, fixture.sources = runner.load_task(fixture.root, fixture.task_path)
        policies, raised, disposed = [], [], []
        actual_policy = completion.GenerationRecoveryPolicy
        actual_validate = runner.validate_result
        actual_context = runner.proposal_cli_context

        def policy_factory(check):
            policy = actual_policy(check)
            policies.append(policy)
            return policy

        def observe_validate(*args, **kwargs):
            try:
                return actual_validate(*args, **kwargs)
            except runner.ProposalError as error:
                raised.append(error)
                raise

        @contextmanager
        def observe_context(*args, **kwargs):
            disposable = None
            try:
                with actual_context(*args, **kwargs) as value:
                    disposable = value[1].parent
                    yield value
            finally:
                disposed.append(disposable is not None and not disposable.exists())

        def completed(_process, _input, output):
            assert len(policies) == 1
            return policies[0]._check(output, b"")

        raw = _wire(INVALID, cid="session-one")
        with patch.object(runner, "GenerationRecoveryPolicy", side_effect=policy_factory), \
                patch.object(runner, "validate_result", side_effect=observe_validate), \
                patch.object(runner, "proposal_cli_context", side_effect=observe_context):
            with pytest.raises(runner.ProposalError) as caught:
                fixture.invoke_fake_cli(run_id="terminal-budget-publication", raw_output=raw,
                                        capture_completion=completed)
        assert len(raised) == 1 and caught.value is raised[0], str(caught.value)
        assert type(caught.value) is runner.ProposalError and str(caught.value) == MESSAGE
        out = fixture.root / ".local/two-site-refactor/proposals/terminal-budget-publication"
        status = json.loads((out / "status.json").read_text())
        assert status["state"] == "BLOCKED" and status["error"] == "ProposalError: " + MESSAGE
        assert disposed == [True]
        assert not any((out / name).exists() for name in
                       ("result.json", "proposal.patch", "transport-completion.json"))
    finally:
        fixture.doCleanups()


# Exact compatibility for the preexisting Session-negation fix and this rejection fix.
_PRE797_SOURCE = "351620643267876d1f33768d1f47be46d0520b54a64839e782b62f914b9ed62d"
_PRE797_AST = "3e28dba958f4c3423391c7d4e876a176f1e221c62f4784d662a33e8b8e87b853"
_SOURCE797 = "003098d03dca017b443e6c3ee4b68505233d5ea5943aaff8046b9bd6712cc094"
_AST797 = "7a684b9a3f5532ba64c1dd83194757781c277e54ecf3c273af801192d3d5a937"
_COMPAT797_SHA = "aae1d1cf9ce530bd12edb7950a486b790b3fa43d609eca2c479d67988f4d3bc4"
_COMPAT797_BYTES = 8894


def _fixed_797_pair(source, *, rejection=False, inverse=False):
    """Invert only one of two complete byte-pinned pairs; never run SDK code."""
    assert type(source) is bytes and type(rejection) is bool and type(inverse) is bool
    before, after = ((_PRE797_SOURCE, _SOURCE797) if rejection
                     else ("f01b25fd68a423b695c929f921e08007c18326dc3a406aa03b3c0db7b12aacfd", _PRE797_SOURCE))
    expected_in, expected_out = (after, before) if inverse else (before, after)
    assert _compat797_digest(source) == expected_in, "unknown or partial fixed797 source"
    material = _compat797_material()
    rows = material["rejection797" if rejection else "pre797"]
    assert type(rows) is list and len(rows) == (4 if rejection else 2)
    text = source.decode("utf-8", errors="strict")
    for row in reversed(rows) if inverse else rows:
        assert type(row) is dict and set(row) == {"before", "after"}
        assert all(type(row[key]) is str and row[key] for key in ("before", "after"))
        old, new = (row["after"], row["before"]) if inverse else (row["before"], row["after"])
        assert text.count(old) == 1, "fixed797 inverse hunk is not unique"
        text = text.replace(old, new, 1)
    result = text.encode("utf-8", errors="strict")
    assert _compat797_digest(result) == expected_out, "fixed797 exact inverse failed"
    return result


def _historical752_source(source):
    """Return genuine historical bytes, not a stand-in for the actual SDK."""
    assert type(source) is bytes
    assert _compat797_digest(source) in {_SOURCE797, _PRE797_SOURCE, seventh.SOURCE_752_SHA}
    if _compat797_digest(source) == _SOURCE797:
        previous = _fixed_797_pair(source, rejection=True, inverse=True)
        assert _fixed_797_pair(previous, rejection=True) == source
        source = previous
    if _compat797_digest(source) == _PRE797_SOURCE:
        previous = _fixed_797_pair(source, inverse=True)
        assert _fixed_797_pair(previous) == source
        source = previous
    assert _compat797_digest(source) == seventh.SOURCE_752_SHA
    return source


def _compat797_digest(body):
    return hashlib.sha256(body).hexdigest()


def _compat797_material():
    asset = Path(__file__).resolve().parent / "fixtures/two_site_json_prompt_fidelity_737k_797r_inverse.json"
    with asset.open("rb") as stream:
        raw = stream.read(_COMPAT797_BYTES + 1)
    assert len(raw) == _COMPAT797_BYTES and _compat797_digest(raw) == _COMPAT797_SHA
    material = json.loads(raw)
    assert type(material) is dict and set(material) == {"pre797", "rejection797"}
    return material


def _assert_actual797_compatibility():
    from scripts import two_site_json_prompt_fidelity as fidelity
    from tests import test_two_site_json_prompt_fidelity_737k as historical_fixture

    runner, _ = historical_fixture._three_versions()
    actual = fidelity._read_runner_source()
    assert _compat797_digest(actual) == _SOURCE797
    assert _compat797_digest(ast.dump(historical_fixture._run_node(actual), include_attributes=True).encode()) == _AST797
    assert fidelity._validated_run_code(actual, runner.run).co_name == "run"
    before_namespace = {name: id(value) for name, value in vars(runner).items()}
    previous = _fixed_797_pair(actual, rejection=True, inverse=True)
    historical = _fixed_797_pair(previous, inverse=True)
    assert _compat797_digest(previous) == _PRE797_SOURCE
    assert _compat797_digest(historical) == "f01b25fd68a423b695c929f921e08007c18326dc3a406aa03b3c0db7b12aacfd"
    assert _fixed_797_pair(historical) == previous
    assert _fixed_797_pair(previous, rejection=True) == actual
    assert _historical752_source(actual) == historical
    assert _compat797_digest(ast.dump(historical_fixture._run_node(previous), include_attributes=True).encode()) == _PRE797_AST
    for body in (historical, previous):
        assert fidelity._validated_run_code(body, historical_fixture._unexecuted_original(runner, body)).co_name == "run"
        with pytest.raises(fidelity.PromptFidelityError, match="actual SDK run code mismatch"):
            fidelity._validated_run_code(body, runner.run)
    rows = _compat797_material()
    for rejection, left, right, key in (
        (False, historical, previous, "pre797"),
        (True, previous, actual, "rejection797"),
    ):
        for row in rows[key]:
            old, new = row["before"].encode(), row["after"].encode()
            assert left.count(old) == right.count(new) == 1
            for partial in (left.replace(old, new, 1), right.replace(new, old, 1)):
                assert _compat797_digest(partial) not in fidelity._RUNNER_IDENTITIES
                with pytest.raises(AssertionError, match="unknown or partial"):
                    _fixed_797_pair(partial, rejection=rejection)
                with pytest.raises(fidelity.PromptFidelityError, match="SDK source pin mismatch"):
                    fidelity._validated_run_code(partial, runner.run)
        with pytest.raises(AssertionError, match="unknown or partial"):
            _fixed_797_pair(right + b"\n", rejection=rejection, inverse=True)
    assert before_namespace == {name: id(value) for name, value in vars(runner).items()}


# Real Python children below are synthetic failure evidence, never provider proof.
OUTPUT_LIMIT_MESSAGE = "MODEL_OUTPUT_TRUNCATED: rescope to a smaller task"


def _output_limit_events(role="worker"):
    declared = runner.schema(role, _task(), _sources(), original_sources=_sources())
    return [
        {"event": "init", "conversation_id": CID,
         "init": {"model": runner.WORKER_MODEL, "json_schema": declared,
                  "tools": ["finish", "read_file"]}},
        {"event": "step_update", "step_update": {
            "conversation_id": CID, "step_index": 0,
            "step_type": "user_input", "state": "DONE"}},
        {"event": "step_update", "step_update": {
            "conversation_id": CID, "step_index": 1,
            "step_type": "agent_response", "state": "DONE"}},
        {"event": "result", "result": {
            "conversation_id": CID, "status": "ERROR", "num_turns": 1,
            "response": "", "json_schema": json.loads(json.dumps(declared)),
            "error": "Your previous response was cut off because it exceeded the output token limit"}},
    ]


def _encode_output_limit(events):
    return b"".join(json.dumps(event).encode() + b"\n" for event in events)


@pytest.mark.parametrize("role", ["worker", "verifier"])
def test_bound_output_limit_raises_only_existing_nonretryable_error(role):
    raw = _encode_output_limit(_output_limit_events(role))
    with pytest.raises(runner.ProposalError, match="^MODEL_OUTPUT_TRUNCATED:") as caught:
        _check(raw, role=role)
    assert type(caught.value) is runner.ProposalError
    assert str(caught.value) == OUTPUT_LIMIT_MESSAGE
    assert not hasattr(caught.value, "quota_evidence")


@pytest.mark.parametrize("fault", [
    "init-cid", "terminal-cid", "nested-cid", "outer-cid", "model",
    "init-schema", "terminal-schema", "schema-type-alias", "missing-schema",
    "duplicate-init", "duplicate-terminal", "late-event", "malformed-step",
    "tool", "top-tool", "subagent", "denial", "terminal-denial", "bool-turn", "wrong-turn",
    "quota", "auth", "unknown", "explicit-error", "nonobject", "nonfinite",
])
def test_unqualified_error_does_not_gain_early_rejection(fault):
    events = _output_limit_events()
    if fault in {"init-cid", "outer-cid"}:
        events[0 if fault == "init-cid" else 1]["conversation_id"] = "foreign"
    elif fault == "terminal-cid":
        events[-1]["result"]["conversation_id"] = "foreign"
    elif fault == "nested-cid":
        events[1]["step_update"]["conversation_id"] = "foreign"
    elif fault == "model":
        events[0]["init"]["model"] = "wrong-model"
    elif fault in {"init-schema", "terminal-schema"}:
        obj = events[0]["init"] if fault == "init-schema" else events[-1]["result"]
        obj["json_schema"] = {"type": "string"}
    elif fault == "schema-type-alias":
        events[-1]["result"]["json_schema"]["additionalProperties"] = 0
    elif fault == "missing-schema":
        del events[-1]["result"]["json_schema"]
    elif fault == "duplicate-init":
        events.insert(1, events[0])
    elif fault == "duplicate-terminal":
        events.append(events[-1])
    elif fault == "late-event":
        events.append({"event": "agent_response"})
    elif fault == "malformed-step":
        events[1]["step_update"] = "malformed"
    elif fault == "tool":
        events[1]["step_update"].update(step_type="tool", tool_name="finish")
    elif fault == "top-tool":
        events[1]["tool_info"] = {}
    elif fault == "subagent":
        events[1]["step_update"]["subagent_info"] = {}
    elif fault == "denial":
        events[1]["denied_actions"] = ["read"]
    elif fault == "terminal-denial":
        events[-1]["result"]["denied_actions"] = ["read"]
    elif fault == "bool-turn":
        events[-1]["result"]["num_turns"] = True
    elif fault == "wrong-turn":
        events[-1]["result"]["num_turns"] = 2
    elif fault in {"quota", "auth", "unknown"}:
        events[-1]["result"]["error"] = {
            "quota": "quota exhausted; output token limit exceeded",
            "auth": "authentication failed; output token limit exceeded",
            "unknown": "unrecognized backend error",
        }[fault]
    elif fault == "explicit-error":
        events.insert(1, {"event": "error"})
    elif fault == "nonobject":
        events.insert(1, [])
    elif fault == "nonfinite":
        events[1]["malformed_number"] = float("nan")
    assert _check(_encode_output_limit(events)) is False


@pytest.mark.parametrize("fault", ["no-lf", "truncated", "duplicate-key", "unicode", "stderr"])
def test_malformed_or_stderr_error_keeps_old_path(fault):
    raw = _encode_output_limit(_output_limit_events())
    stderr = b""
    if fault == "no-lf":
        raw = raw[:-1]
    elif fault == "truncated":
        raw = raw[:-9]
    elif fault == "duplicate-key":
        raw = raw.replace(b'"status": "ERROR"', b'"status": "ERROR", "status": "ERROR"')
    elif fault == "unicode":
        raw += b"\xff\n"
    else:
        stderr = b"existing stderr evidence\n"
    assert _check(raw, stderr) is False


def test_real_lingering_output_limit_is_blocked_and_owned_group_cleaned(tmp_path):
    raw = _encode_output_limit(_output_limit_events())
    policy = completion.GenerationRecoveryPolicy(_check)
    with _child(tmp_path, raw) as process:
        started = time.monotonic()
        with pytest.raises(runner.ProposalError) as caught:
            _capture(process, tmp_path, policy)
        assert time.monotonic() - started < 5.0
        assert type(caught.value) is runner.ProposalError
        assert str(caught.value) == OUTPUT_LIMIT_MESSAGE
        assert process.returncode == -signal.SIGKILL
        _assert_rejected_cleanup(process, tmp_path, policy)
        before = policy.failure_facts["before_cleanup"]
        assert before["terminal_read_observed"] is True
        assert before["input_written_bytes"] == len(INPUT)
        assert before["input_writer_close_observed"] is True
        assert policy.failure_facts["after_cleanup"]["owned_group_absent"] is True
        assert before["stdout_sha256"] == hashlib.sha256(raw).hexdigest()
    assert (tmp_path / "stdout.bin").read_bytes() == raw
    assert (tmp_path / "stderr.bin").read_bytes() == b""


def test_error_before_full_input_cannot_use_qualified_shortcut(tmp_path):
    raw = _encode_output_limit(_output_limit_events())
    calls = []
    def observe(stdout, stderr):
        calls.append(True)
        return _check(stdout, stderr)
    policy = completion.GenerationRecoveryPolicy(observe)
    with _child(tmp_path, raw, read_input=False, hang=False) as process:
        with pytest.raises(runner.ProposalError, match="input was not completely written"):
            _capture(process, tmp_path, policy, data=b"x" * (2 * 1024 * 1024))
        assert calls == []
        assert policy.failure_facts["before_cleanup"]["input_writer_close_observed"] is False
        assert policy.failure_facts["before_cleanup"]["terminal_read_observed"] is False
    assert (tmp_path / "stdout.bin").read_bytes() == raw


def test_stdout_is_visible_before_observer_and_process_exit(tmp_path):
    seen = []
    def observe(stdout, stderr):
        assert (tmp_path / "stdout.bin").read_bytes() == stdout
        seen.append(True)
        return _check(stdout, stderr)
    raw = _encode_output_limit(_output_limit_events())
    policy = completion.GenerationRecoveryPolicy(observe)
    with _child(tmp_path, raw) as process:
        with pytest.raises(runner.ProposalError, match="^MODEL_OUTPUT_TRUNCATED:"):
            _capture(process, tmp_path, policy)
        _assert_rejected_cleanup(process, tmp_path, policy)
    assert seen == [True]
