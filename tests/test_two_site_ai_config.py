"""Closed policy/runtime configuration, with no credential or provider access."""
import copy
import json
import os
from pathlib import Path

import pytest

from scripts import two_site_ai_config as config

TEMPLATE = Path(__file__).resolve().parents[1] / config.AI_CONFIG_REL


def write_policy(root, value=None):
    path = root / config.AI_CONFIG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.read_text() if value is None else json.dumps(value))
    path.chmod(0o600)
    return path


def write_runtime(root, value=None):
    value = value or {"schema_version": 1, "coordinator": {
        "path": "/synthetic/qodercli", "version": "1.2.3", "sha256": "a" * 64}}
    path = root / config.AI_RUNTIME_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value)); path.chmod(0o600)
    return path


def test_roles_are_loaded_only_from_json_and_results_are_detached(tmp_path):
    path = write_policy(tmp_path)
    original = json.loads(path.read_text())
    for role in config.ROLES:
        assert config.role_settings(tmp_path, role) == original["roles"][role]
    modified = config.load_ai_config(tmp_path)
    modified["roles"]["worker"]["model"] = "synthetic-change"
    assert config.load_ai_config(tmp_path) == original
    original["roles"]["worker"]["model"] = "synthetic-qualified-model"
    path.write_text(json.dumps(original))
    assert config.role_settings(tmp_path, "worker")["model"] == "synthetic-qualified-model"


def test_new_qoder_model_and_catalog_max_effort_need_only_json_edit(tmp_path):
    path = write_policy(tmp_path); data = json.loads(path.read_text())
    data["roles"]["coordinator"].update(model="synthetic-new-model", reasoning_effort="max")
    path.write_text(json.dumps(data))
    assert config.role_settings(tmp_path, "coordinator") == {
        "provider": "qoder", "model": "synthetic-new-model", "reasoning_effort": "max"}


def test_qoder_heavy_tasks_are_selected_from_policy_and_detached(tmp_path):
    path = write_policy(tmp_path); data = json.loads(path.read_text())
    assert config.qoder_task_settings(tmp_path, "proposal_author")["model"] == "Qwen3.8-Max"
    assert config.qoder_task_settings(tmp_path, "schema_final_review")["model"] == "Qwen3.8-Max"
    selected = config.qoder_task_settings(tmp_path, "proposal_author")
    selected["model"] = "tampered"
    assert config.qoder_task_settings(tmp_path, "proposal_author") == data["qoder_tasks"]["proposal_author"]


def test_qoder_task_falls_back_to_owner_role_for_legacy_policy(tmp_path):
    path = write_policy(tmp_path); data = json.loads(path.read_text()); data.pop("qoder_tasks")
    path.write_text(json.dumps(data))
    assert config.qoder_task_settings(tmp_path, "proposal_author") == data["roles"]["coordinator"]
    assert config.qoder_task_settings(tmp_path, "schema_final_review") == data["roles"]["final_reviewer"]


@pytest.mark.parametrize("tasks,code", [
    ({}, "AI_CONFIG_QODER_TASKS"),
    ({"unknown": {"provider": "qoder", "model": "x", "reasoning_effort": "max"}},
     "AI_CONFIG_QODER_TASKS"),
    ({"proposal_author": {"provider": "antigravity", "model": "x", "reasoning_effort": "max"}},
     "AI_CONFIG_QODER_TASK_PROVIDER"),
    ({"proposal_author": {"provider": "qoder", "model": "bad\nmodel", "reasoning_effort": "max"}},
     "AI_CONFIG_QODER_TASK_MODEL"),
])
def test_qoder_task_policy_fails_closed(tmp_path, tasks, code):
    path = write_policy(tmp_path); data = json.loads(path.read_text()); data["qoder_tasks"] = tasks
    path.write_text(json.dumps(data))
    with pytest.raises(config.AIConfigError, match=code):
        config.load_ai_config(tmp_path)


def test_unknown_qoder_task_is_rejected(tmp_path):
    write_policy(tmp_path)
    with pytest.raises(config.AIConfigError, match="AI_CONFIG_QODER_TASK"):
        config.qoder_task_settings(tmp_path, "unbounded")


@pytest.mark.parametrize("mutation", [
    lambda v: v.update(schema_version=True),
    lambda v: v.update(secret="forbidden"),
    lambda v: v["roles"].pop("reviewer"),
    lambda v: v["roles"]["worker"].update(provider="qoder"),
    lambda v: v["roles"]["coordinator"].update(model="bad\nmodel"),
    lambda v: v["roles"]["reviewer"].update(reasoning_effort="unqualified"),
    lambda v: v["roles"]["reviewer"].update(api_key="forbidden"),
])
def test_policy_shape_and_values_fail_closed(tmp_path, mutation):
    value = json.loads(TEMPLATE.read_text()); mutation(value); write_policy(tmp_path, value)
    with pytest.raises(config.AIConfigError): config.load_ai_config(tmp_path)


def test_missing_policy_never_uses_environment_or_embedded_model_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("TWO_SITE_AI_CONFIG", str(TEMPLATE))
    with pytest.raises(config.AIConfigError): config.load_ai_config(tmp_path)
    write_policy(tmp_path)
    with pytest.raises(config.AIConfigError): config.role_settings(tmp_path, "verifier")


@pytest.mark.parametrize("raw", [b'{"schema_version":1,"schema_version":1}', b'{"schema_version":NaN}'])
def test_duplicate_or_nonfinite_json_is_rejected(tmp_path, raw):
    path = write_policy(tmp_path); path.write_bytes(raw)
    with pytest.raises(config.AIConfigError): config.load_ai_config(tmp_path)


@pytest.mark.parametrize("value", [True, 32767, 1048577, "262144", None])
def test_qoder_output_budget_is_typed_and_bounded(tmp_path, value):
    path = write_policy(tmp_path); data = json.loads(path.read_text())
    data["qoder_execution"] = {"max_output_tokens": value}
    path.write_text(json.dumps(data)); path.chmod(0o600)
    with pytest.raises(config.AIConfigError, match="AI_CONFIG_QODER_OUTPUT_TOKENS"):
        config.load_ai_config(tmp_path)


def test_qoder_execution_rejects_open_shape(tmp_path):
    path = write_policy(tmp_path); data = json.loads(path.read_text())
    data["qoder_execution"] = {"max_output_tokens": 262144, "model": "hardcoded"}
    path.write_text(json.dumps(data)); path.chmod(0o600)
    with pytest.raises(config.AIConfigError, match="AI_CONFIG_QODER_EXECUTION_FIELDS"):
        config.load_ai_config(tmp_path)


@pytest.mark.parametrize("value", [True, 1023, 262144, "32768", None])
def test_qoder_thinking_budget_is_typed_bounded_and_below_output(tmp_path, value):
    path = write_policy(tmp_path); data = json.loads(path.read_text())
    data["qoder_execution"]["max_thinking_tokens"] = value
    path.write_text(json.dumps(data)); path.chmod(0o600)
    with pytest.raises(config.AIConfigError, match="AI_CONFIG_QODER_THINKING_TOKENS"):
        config.load_ai_config(tmp_path)


def test_qoder_role_thinking_budgets_are_closed_and_detached(tmp_path):
    path = write_policy(tmp_path); data = json.loads(path.read_text())
    loaded = config.load_ai_config(tmp_path)
    assert loaded["qoder_execution"]["role_max_thinking_tokens"] == {
        "proposal_author": 65536, "final_reviewer": 65536}
    loaded["qoder_execution"]["role_max_thinking_tokens"]["proposal_author"] = 1024
    assert config.load_ai_config(tmp_path) == data


@pytest.mark.parametrize("budgets,code", [
    ({}, "AI_CONFIG_QODER_ROLE_THINKING_FIELDS"),
    ({"unknown": 65536}, "AI_CONFIG_QODER_ROLE_THINKING_FIELDS"),
    ({"proposal_author": True}, "AI_CONFIG_QODER_ROLE_THINKING_TOKENS"),
    ({"proposal_author": 1023}, "AI_CONFIG_QODER_ROLE_THINKING_TOKENS"),
    ({"proposal_author": 262144}, "AI_CONFIG_QODER_ROLE_THINKING_TOKENS"),
])
def test_qoder_role_thinking_budgets_fail_closed(tmp_path, budgets, code):
    path = write_policy(tmp_path); data = json.loads(path.read_text())
    data["qoder_execution"]["role_max_thinking_tokens"] = budgets
    path.write_text(json.dumps(data)); path.chmod(0o600)
    with pytest.raises(config.AIConfigError, match=code):
        config.load_ai_config(tmp_path)


@pytest.mark.parametrize("value", [True, -1, 3, "0", None])
def test_qoder_model_request_retries_are_typed_and_bounded(tmp_path, value):
    path = write_policy(tmp_path); data = json.loads(path.read_text())
    data["qoder_execution"]["max_model_request_retries"] = value
    path.write_text(json.dumps(data)); path.chmod(0o600)
    with pytest.raises(config.AIConfigError, match="AI_CONFIG_QODER_MODEL_RETRIES"):
        config.load_ai_config(tmp_path)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "group-write", "oversize"])
def test_unsafe_policy_file_is_rejected(tmp_path, kind):
    path = write_policy(tmp_path)
    if kind == "symlink":
        target = tmp_path / "target"; path.rename(target); path.symlink_to(target)
    elif kind == "hardlink": os.link(path, tmp_path / "alias")
    elif kind == "group-write": path.chmod(0o620)
    else: path.write_bytes(b" " * 65537)
    with pytest.raises(config.AIConfigError): config.load_ai_config(tmp_path)


def test_symlink_parent_is_not_a_configuration_route(tmp_path):
    outside = tmp_path / "outside"; outside.mkdir(); write_policy(outside)
    root = tmp_path / "root"; root.mkdir(); (root / "docs").symlink_to(outside / "docs", target_is_directory=True)
    with pytest.raises(config.AIConfigError): config.load_ai_config(root)


def test_runtime_is_credential_free_private_and_optional_for_policy(tmp_path):
    write_policy(tmp_path)
    assert config.role_settings(tmp_path, "coordinator")
    with pytest.raises(config.AIConfigError): config.load_runtime_config(tmp_path)
    path = write_runtime(tmp_path); expected = json.loads(path.read_text())
    assert config.load_runtime_config(tmp_path) == expected
    path.chmod(0o644)
    with pytest.raises(config.AIConfigError): config.load_runtime_config(tmp_path)
    path.chmod(0o600)
    expected["coordinator"]["access_token"] = "forbidden"; path.write_text(json.dumps(expected))
    with pytest.raises(config.AIConfigError): config.load_runtime_config(tmp_path)


@pytest.mark.parametrize("field,value", [("path", "relative"), ("sha256", "invalid"),
                                         ("version", "x\nsecret"), ("config_dir", "relative")])
def test_bad_runtime_identity_is_rejected(tmp_path, field, value):
    path = write_runtime(tmp_path); data = json.loads(path.read_text())
    data["coordinator"][field] = value; path.write_text(json.dumps(data))
    with pytest.raises(config.AIConfigError): config.load_runtime_config(tmp_path)
