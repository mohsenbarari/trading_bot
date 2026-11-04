"""Synthetic CLI metadata and SDK events; never invoke a real model."""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from scripts import two_site_ai_config as config
from scripts import two_site_qoder_driver as driver

PROJECT = Path(__file__).resolve().parents[1]
TEMPLATE = PROJECT / config.AI_CONFIG_REL
FINAL = {"schema_version": "1.0", "state": "BLOCKED", "disposition": "HALT",
    "category": "EXTERNAL_GATE", "summary": "Synthetic test", "commit": None,
    "next_action": None, "blockers": ["Synthetic gate"], "gemini_worker_run_id": None,
    "gemini_reviewer_run_id": None, "proposal_sha256": None}


def role():
    return json.loads(TEMPLATE.read_text())["roles"]["coordinator"]


def setup_runtime(root):
    schema = root / driver.COORDINATOR_SCHEMA_REL
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_bytes((PROJECT / driver.COORDINATOR_SCHEMA_REL).read_bytes())
    binary = root / "fake-qodercli"
    binary.write_text('#!/usr/bin/python3\nimport json,sys\n'
        'assert \'{"general":{"enableAutoUpdate":false}}\' in sys.argv\n'
        'print("1.2.3" if "--version" in sys.argv else json.dumps('
        '{"logged_in":True,"account":"SYNTHETIC_NOT_RETURNED"}))\n')
    binary.chmod(0o700)
    runtime = {"path": str(binary), "version": "1.2.3", "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    path = root / config.AI_RUNTIME_REL; path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": 1, "coordinator": runtime})); path.chmod(0o600)
    policy = root / config.AI_CONFIG_REL
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_bytes(TEMPLATE.read_bytes()); policy.chmod(0o600)
    return binary, runtime


def events(value=None):
    return [{"type": "system", "subtype": "init", "session_id": "synthetic-session-1",
             "model": role()["model"], "tools": ["Read", "mcp_list", "mcp_get", "mcp_call"],
             "mcp_servers": [], "plugins": []},
            {"type": "system", "subtype": "hook_diagnostics", "session_id": "synthetic-session-1"},
            {"type": "assistant", "session_id": "synthetic-session-1", "message": {
                "role": "assistant", "content": [{"type": "text", "text": "Synthetic"}]}},
            {"type": "result", "subtype": "success", "is_error": False,
             "session_id": "synthetic-session-1", "num_turns": 1, "stop_reason": "end_turn",
             "permission_denials": [], "result": json.dumps(FINAL if value is None else value),
             "usage": {"input_tokens": 0, "output_tokens": 0}, "total_credits": 0.12702}]


def stream(root, values):
    path = root / "stream.jsonl"
    path.write_text("".join(json.dumps(value) + "\n" for value in values)); path.chmod(0o600)
    return path


def test_real_local_metadata_probe_returns_only_public_bound_identity(tmp_path):
    binary, runtime = setup_runtime(tmp_path)
    identity = driver.qoder_identity(binary, runtime, tmp_path, role())
    assert identity == {"provider": "qoder", "path": str(binary), "version": runtime["version"],
        "sha256": runtime["sha256"], "logged_in": True, "auto_update_enabled": False,
        "model": role()["model"], "reasoning": role()["reasoning_effort"]}
    assert "SYNTHETIC_NOT_RETURNED" not in json.dumps(identity)


@pytest.mark.parametrize("failure", ["wrong-pin", "wrong-version", "logged-out", "not-boolean", "changed-after-probe"])
def test_identity_failures_never_admit_a_runtime(tmp_path, monkeypatch, failure):
    binary, runtime = setup_runtime(tmp_path)
    def probe(argv, temporary):
        if "--version" in argv:
            return b"wrong" if failure == "wrong-version" else b"1.2.3"
        if failure == "changed-after-probe": binary.write_bytes(binary.read_bytes() + b"#changed\n")
        return json.dumps({"logged_in": False if failure == "logged-out" else 1 if failure == "not-boolean" else True}).encode()
    monkeypatch.setattr(driver, "_probe", probe)
    if failure == "wrong-pin": runtime["sha256"] = "0" * 64
    with pytest.raises(driver.QoderError): driver.qoder_identity(binary, runtime, tmp_path)


def test_metadata_probe_output_is_bounded_in_ram(tmp_path):
    binary = tmp_path / "noisy"
    binary.write_text('#!/usr/bin/python3\nimport sys\nsys.stdout.write("x"*70000)\n'); binary.chmod(0o700)
    with pytest.raises(driver.QoderError, match="QODER_PROBE_OUTPUT_BOUND"):
        driver._probe([str(binary)], tmp_path)


def test_command_has_no_prompt_in_argv_no_resume_and_explicit_minimal_policy(tmp_path):
    binary, _ = setup_runtime(tmp_path); run_dir = tmp_path / "run"; run_dir.mkdir()
    prompt = "SYNTHETIC STDIN ONLY PROMPT"
    argv = driver.qoder_command(binary, tmp_path, run_dir, prompt, role())
    assert prompt not in argv
    assert argv[argv.index("--model") + 1] == role()["model"]
    assert argv[argv.index("--reasoning-effort") + 1] == role()["reasoning_effort"]
    assert "--thinking" not in argv
    assert argv[argv.index("--thinking-budget") + 1] == "32768"
    assert argv[argv.index("--max-model-request-retries") + 1] == "0"
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings == {"general": {"enableAutoUpdate": False}, "modelConfigs": {
        "customOverrides": [{"match": {"model": role()["model"], "overrideScope": "core"},
            "modelConfig": {"generateContentConfig": {"maxOutputTokens": 262144}}}]}}
    assert argv[argv.index("--permission-mode") + 1] == "auto"
    assert "--dangerously-skip-permissions" not in argv
    assert {"--print", "--no-session-persistence", "--strict-mcp-config"} <= set(argv)
    assert not {"--continue", "--resume", "--session-id", "--remote"} & set(argv)
    assert argv[argv.index("--tools") + 1:-1] == list(driver.TOOLS["coordinator"])
    readonly = driver.qoder_command(binary, tmp_path, run_dir, prompt, role(), role="final_reviewer")
    assert readonly[readonly.index("--tools") + 1:-1] == [""]
    assert readonly[readonly.index("--thinking-budget") + 1] == "65536"
    assert readonly[readonly.index("--max-model-request-retries") + 1] == "0"
    author = driver.qoder_command(binary, tmp_path, run_dir, prompt, role(),
        role="proposal_author", response_schema={"type": "object", "additionalProperties": False,
            "required": ["value"], "properties": {"value": {"type": "string"}}})
    assert author[author.index("--thinking-budget") + 1] == "65536"
    assert author[author.index("--max-model-request-retries") + 1] == "0"
    assert list(run_dir.iterdir()) == []


def test_json_only_new_qoder_model_max_effort_reaches_real_argv_builder(tmp_path):
    binary, _ = setup_runtime(tmp_path); run_dir = tmp_path / "run"; run_dir.mkdir()
    policy = json.loads(TEMPLATE.read_text())
    policy["roles"]["coordinator"].update(model="synthetic-new-model", reasoning_effort="max")
    path = tmp_path / config.AI_CONFIG_REL; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy)); path.chmod(0o600)
    selected = config.role_settings(tmp_path, "coordinator")
    argv = driver.qoder_command(binary, tmp_path, run_dir, "Synthetic prompt", selected)
    assert argv[argv.index("--model") + 1] == "synthetic-new-model"
    assert argv[argv.index("--reasoning-effort") + 1] == "max"
    assert argv[argv.index("--thinking-budget") + 1] == "32768"
    assert argv[argv.index("--max-model-request-retries") + 1] == "0"
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings["modelConfigs"]["customOverrides"][0]["match"]["model"] == "synthetic-new-model"
    assert settings["modelConfigs"]["customOverrides"][0]["modelConfig"] == {
        "generateContentConfig": {"maxOutputTokens": 262144}}


def test_historical_policy_without_execution_override_keeps_minimal_settings(tmp_path):
    binary, _ = setup_runtime(tmp_path); run_dir = tmp_path / "run"; run_dir.mkdir()
    policy_path = tmp_path / config.AI_CONFIG_REL
    policy = json.loads(policy_path.read_text()); policy.pop("qoder_execution")
    policy_path.write_text(json.dumps(policy)); policy_path.chmod(0o600)
    argv = driver.qoder_command(binary, tmp_path, run_dir, "Synthetic", role())
    assert json.loads(argv[argv.index("--settings") + 1]) == {
        "general": {"enableAutoUpdate": False}}
    assert argv[argv.index("--thinking") + 1] == "adaptive"
    assert "--thinking-budget" not in argv
    assert "--max-model-request-retries" not in argv


def test_only_genuine_bound_terminal_json_creates_result(tmp_path):
    stream(tmp_path, events())
    assert driver.finalize_qoder_output(tmp_path, expected_model=role()["model"]) == FINAL
    assert json.loads((tmp_path / "result.json").read_text()) == FINAL
    assert stat.S_IMODE((tmp_path / "result.json").stat().st_mode) == 0o600


@pytest.mark.parametrize("fault", [
    "missing-init", "missing-result", "two-init", "two-result", "late-event", "error-subtype",
    "error-flag", "false-is-zero", "cross-session", "nested-cross-session", "wrong-model",
    "event-error", "malformed-message", "tool-task", "tool-mcp", "stream-tool-task",
    "nonempty-mcp", "plugin", "denied-action", "token-stop", "bad-turn-count",
    "not-json", "fenced-json", "array-result", "duplicate-json", "nonfinite-json", "oversize-result",
])
def test_malformed_failed_truncated_or_unauthorized_events_never_write_result(tmp_path, fault):
    values = copy.deepcopy(events())
    if fault == "missing-init": values.pop(0)
    elif fault == "missing-result": values.pop()
    elif fault == "two-init": values.insert(1, copy.deepcopy(values[0]))
    elif fault == "two-result": values.append(copy.deepcopy(values[-1]))
    elif fault == "late-event": values.append({"type": "system", "subtype": "late"})
    elif fault == "error-subtype": values[-1]["subtype"] = "error_during_execution"
    elif fault == "error-flag": values[-1]["is_error"] = True
    elif fault == "false-is-zero": values[-1]["is_error"] = 0
    elif fault == "cross-session": values[2]["session_id"] = "different"
    elif fault == "nested-cross-session": values[2]["message"]["session_id"] = "different"
    elif fault == "wrong-model": values[0]["model"] = "different-model"
    elif fault == "event-error": values.insert(2, {"type": "error"})
    elif fault == "malformed-message": values[2]["message"] = "bad"
    elif fault in {"tool-task", "tool-mcp"}:
        values[2]["message"]["content"] = [{"type": "tool_use", "name": "Task" if fault == "tool-task" else "mcp_call"}]
    elif fault == "stream-tool-task": values.insert(2, {"type": "stream_event", "event": {"content_block": {"type": "tool_use", "name": "Task"}}})
    elif fault == "nonempty-mcp": values[0]["mcp_servers"] = [{"name": "unexpected"}]
    elif fault == "plugin": values[0]["plugins"] = [{"name": "unexpected"}]
    elif fault == "denied-action": values[-1]["permission_denials"] = [{"tool_name": "Bash"}]
    elif fault == "token-stop": values[-1]["stop_reason"] = "max_tokens"
    elif fault == "bad-turn-count": values[-1]["num_turns"] = True
    elif fault == "not-json": values[-1]["result"] = "not JSON"
    elif fault == "fenced-json": values[-1]["result"] = "```json\n{}\n```"
    elif fault == "array-result": values[-1]["result"] = "[]"
    elif fault == "duplicate-json": values[-1]["result"] = '{"x":1,"x":2}'
    elif fault == "nonfinite-json": values[-1]["result"] = '{"x":NaN}'
    else: values[-1]["result"] = json.dumps({"large": "x" * 64000})
    stream(tmp_path, values)
    with pytest.raises(driver.QoderError): driver.finalize_qoder_output(tmp_path, expected_model=role()["model"])
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("kind", ["incomplete-line", "partial-json", "symlink", "hardlink", "existing-result"])
def test_capture_truncation_and_artifact_custody_fail_closed(tmp_path, kind):
    path = stream(tmp_path, events())
    if kind == "incomplete-line": path.write_bytes(path.read_bytes().rstrip(b"\n"))
    elif kind == "partial-json": path.write_bytes(path.read_bytes() + b'{"type":')
    elif kind == "symlink":
        target = tmp_path / "alias"; path.rename(target); path.symlink_to(target)
    elif kind == "hardlink": os.link(path, tmp_path / "alias")
    else: (tmp_path / "result.json").write_text("ORIGINAL")
    with pytest.raises(driver.QoderError): driver.finalize_qoder_output(tmp_path)
    if kind == "existing-result": assert (tmp_path / "result.json").read_text() == "ORIGINAL"


def test_allowed_tool_activity_is_not_confused_with_proposal_only_policy(tmp_path):
    values = events(); values[2]["message"]["content"] = [{"type": "tool_use", "name": "Bash", "input": {"command": "synthetic"}}]
    stream(tmp_path, values)
    assert driver.finalize_qoder_output(tmp_path) == FINAL


@pytest.mark.parametrize("name", ["Read", "Glob", "Grep", "Edit", "Bash", "mcp_call"])
def test_final_reviewer_cannot_use_any_tool(tmp_path, name):
    values = events(); values[0]["tools"] = ["mcp_list"]
    values[2]["message"]["content"] = [{"type": "tool_use", "name": name}]
    stream(tmp_path, values)
    with pytest.raises(driver.QoderError, match="QODER_FORBIDDEN_TOOL"):
        driver.finalize_qoder_output(tmp_path, role="final_reviewer")


def test_coordinator_system_instruction_embeds_actual_complete_closed_schema(tmp_path):
    from scripts.two_site_codex_autopilot import SCHEMA_REL
    assert driver.COORDINATOR_SCHEMA_REL == SCHEMA_REL
    binary, _ = setup_runtime(tmp_path); run_dir = tmp_path / "run"; run_dir.mkdir()
    argv = driver.qoder_command(binary, tmp_path, run_dir, "STDIN PRIVATE SYNTHETIC", role())
    assert argv.count("--append-system-prompt") == 1
    system = argv[argv.index("--append-system-prompt") + 1]
    schema = json.loads(system.split("TERMINAL_RESULT_SCHEMA_JSON:\n", 1)[1])
    assert schema == json.loads((PROJECT / SCHEMA_REL).read_bytes())
    assert schema["additionalProperties"] is False and len(schema["required"]) == 11
    keys = json.loads(system.split("REQUIRED_KEYS_JSON=", 1)[1].split("\n", 1)[0])
    assert keys == schema["required"] and set(keys) == set(FINAL)
    assert "ONLY one JSON object" in system and "never invent acceptance" in system
    assert "remaining plan work requires CONTINUE" in system
    assert "entire canonical approved two-site plan" in system
    assert "STDIN PRIVATE SYNTHETIC" not in " ".join(argv)
    final_argv = driver.qoder_command(binary, tmp_path, run_dir, "Final role's own four-key contract", role(), role="final_reviewer")
    assert final_argv.count("--append-system-prompt") == 1
    final_system = final_argv[final_argv.index("--append-system-prompt") + 1]
    final_schema = json.loads(final_system.split("TERMINAL_RESULT_SCHEMA_JSON:\n", 1)[1])
    assert final_schema["additionalProperties"] is False
    assert final_schema["required"] == ["decision", "findings", "proposal_sha256", "source_head"]
    assert set(final_schema["properties"]) == set(final_schema["required"])
    assert "tool-free final semantic reviewer" in final_system
    assert "Final role's own four-key contract" not in " ".join(final_argv)
    receipt_schema = {"type": "object", "additionalProperties": False,
        "required": ["verdict"], "properties": {"verdict": {"enum": ["APPROVE"]}}}
    receipt_argv = driver.qoder_command(binary, tmp_path, run_dir, "Schema review", role(),
        role="final_reviewer", response_schema=receipt_schema)
    receipt_system = receipt_argv[receipt_argv.index("--append-system-prompt") + 1]
    assert json.loads(receipt_system.split("TERMINAL_RESULT_SCHEMA_JSON:\n", 1)[1]) == receipt_schema


@pytest.mark.parametrize("selected_role", ["coordinator", "receipt_formatter"])
def test_only_schema_roles_accept_a_dynamic_response_schema(tmp_path, selected_role):
    binary, _ = setup_runtime(tmp_path); run_dir = tmp_path / "run"; run_dir.mkdir()
    with pytest.raises(driver.QoderError, match="QODER_RESPONSE_SCHEMA_ROLE"):
        driver.qoder_command(binary, tmp_path, run_dir, "prompt", role(), role=selected_role,
            response_schema={"type": "object", "additionalProperties": False})


def test_formatter_is_a_tool_free_capability_profile_using_configured_coordinator(tmp_path):
    binary, _ = setup_runtime(tmp_path); run_dir = tmp_path / "run"; run_dir.mkdir()
    argv = driver.qoder_command(binary, tmp_path, run_dir, "Synthetic format-only request", role(), role="receipt_formatter")
    assert argv[argv.index("--tools") + 1:-1] == [""]
    assert argv[argv.index("--model") + 1] == role()["model"]
    assert argv[argv.index("--reasoning-effort") + 1] == role()["reasoning_effort"]
    assert "--append-system-prompt" in argv
    values = events(); values[0]["tools"] = ["mcp_call", "mcp_get", "mcp_list"]
    stream(tmp_path, values)
    assert driver.finalize_qoder_output(tmp_path, role="receipt_formatter", write_result=False) == FINAL


@pytest.mark.parametrize("name", ["Read", "Bash", "Write", "Grep", "mcp_call"])
def test_formatter_rejects_any_actual_tool_execution(tmp_path, name):
    values = events(); values[0]["tools"] = ["mcp_list"]
    values[2]["message"]["content"] = [{"type": "tool_use", "name": name, "input": {}}]
    stream(tmp_path, values)
    with pytest.raises(driver.QoderError, match="QODER_FORBIDDEN_TOOL"):
        driver.finalize_qoder_output(tmp_path, role="receipt_formatter")
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("bad", ["missing", "open-shape", "incomplete-required", "duplicate-required"])
def test_missing_or_unsafe_coordinator_schema_fails_before_any_provider(tmp_path, bad):
    binary, _ = setup_runtime(tmp_path); run_dir = tmp_path / "run"; run_dir.mkdir()
    path = tmp_path / driver.COORDINATOR_SCHEMA_REL
    if bad == "missing": path.unlink()
    else:
        value = json.loads(path.read_bytes())
        if bad == "open-shape": value["additionalProperties"] = True
        elif bad == "incomplete-required": value["required"].pop()
        else: value["required"].append(value["required"][0])
        path.write_text(json.dumps(value))
    with pytest.raises(driver.QoderError):
        driver.qoder_command(binary, tmp_path, run_dir, "Synthetic", role())
    assert list(run_dir.iterdir()) == []


@pytest.mark.parametrize("text", ["The work is complete.", "```json\n{}\n```", '{"incomplete":', "[]", "null"])
def test_contract_only_body_error_is_typed_after_genuine_full_sdk_validation(tmp_path, text):
    values = events(); values[-1]["result"] = text
    path = stream(tmp_path, values); original = path.read_bytes()
    assert driver.validate_qoder_terminal(tmp_path, expected_model=role()["model"]) == {
        "session_id": "synthetic-session-1", "text": text}
    with pytest.raises(driver.QoderTerminalBodyError) as caught:
        driver.finalize_qoder_output(tmp_path, expected_model=role()["model"])
    assert type(caught.value) is driver.QoderTerminalBodyError
    assert path.read_bytes() == original and not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("fault", ["sdk-json", "truncated-capture", "failed-terminal", "bad-session", "wrong-model", "tool-denial", "forbidden-tool", "output-limit"])
def test_invalid_sdk_is_never_classified_as_terminal_body_only_failure(tmp_path, fault):
    values = events(); values[-1]["result"] = "Human prose cannot hide an invalid envelope"
    if fault == "failed-terminal": values[-1]["is_error"] = True
    elif fault == "bad-session": values[-1]["session_id"] = "other"
    elif fault == "wrong-model": values[0]["model"] = "other"
    elif fault == "tool-denial": values[-1]["permission_denials"] = ["Synthetic"]
    elif fault == "forbidden-tool": values[2]["message"]["content"] = [{"type": "tool_use", "name": "Task"}]
    elif fault == "output-limit": values[-1]["stop_reason"] = "max_tokens"
    path = stream(tmp_path, values)
    if fault == "sdk-json": path.write_bytes(path.read_bytes() + b"not-json\n")
    elif fault == "truncated-capture": path.write_bytes(path.read_bytes().rstrip(b"\n"))
    with pytest.raises(driver.QoderError) as caught:
        driver.finalize_qoder_output(tmp_path, expected_model=role()["model"])
    assert type(caught.value) is driver.QoderError
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', json.dumps({"x":"x"*64000})])
def test_noncanonical_or_oversized_body_is_not_a_prose_format_repair_case(tmp_path, text):
    values = events(); values[-1]["result"] = text; stream(tmp_path, values)
    with pytest.raises(driver.QoderError) as caught:
        driver.finalize_qoder_output(tmp_path)
    assert type(caught.value) is driver.QoderError
    assert not (tmp_path / "result.json").exists()
