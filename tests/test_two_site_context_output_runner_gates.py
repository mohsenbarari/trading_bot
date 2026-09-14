"""Hermetic offline tests for runner context readiness and output contract gates."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from scripts.two_site_proposal_runner import (
    ProposalError,
    _parse_stream,
    check,
    run,
    schema,
    validate_result,
    validate_task_readiness,
)


def _make_mock_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_task(root: Path, filename: str, task: dict) -> Path:
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(task), encoding="utf-8")
    return path


def test_check_and_run_reject_missing_model_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _make_mock_root(tmp_path)
    service_file = root / "scripts/mock_service.py"
    service_file.parent.mkdir(parents=True, exist_ok=True)
    service_file.write_text("from models.order import Order\n", encoding="utf-8")

    task_data = {
        "task_id": "task-missing-model",
        "goal": "Update database persistence for Order records.",
        "context_files": ["scripts/mock_service.py"],
        "allowed_files": ["scripts/mock_service.py"],
        "requirements": ["Persist Order into the database."],
        "test_selectors": ["pytest -q"],
    }
    task_path = _write_task(root, "task.json", task_data)

    with pytest.raises(ProposalError) as exc_check:
        check(root, task_path)
    assert "missing model context: models/order.py" in str(exc_check.value)

    cli_context_invoked = False

    def _trap_cli_context(*args, **kwargs):
        nonlocal cli_context_invoked
        cli_context_invoked = True
        raise AssertionError("proposal_cli_context must not be reached")

    monkeypatch.setattr("scripts.two_site_proposal_runner.validate_runtime_contract", lambda r: {})
    monkeypatch.setattr("scripts.two_site_proposal_runner.proposal_cli_context", _trap_cli_context)

    with pytest.raises(ProposalError) as exc_run:
        run(root, task_path, "worker", "run-fail-gate", 60)
    assert "missing model context: models/order.py" in str(exc_run.value)
    assert not cli_context_invoked


def test_complete_model_context_passes_check(tmp_path: Path):
    root = _make_mock_root(tmp_path)
    service_file = root / "scripts/mock_service.py"
    service_file.parent.mkdir(parents=True, exist_ok=True)
    service_file.write_text("from models.order import Order\n", encoding="utf-8")

    model_file = root / "models/order.py"
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text("class Order:\n    pass\n", encoding="utf-8")

    task_data = {
        "task_id": "task-complete-model",
        "goal": "Update database persistence for Order records.",
        "context_files": ["scripts/mock_service.py", "models/order.py"],
        "allowed_files": ["scripts/mock_service.py"],
        "requirements": ["Persist Order into the database."],
        "test_selectors": ["pytest -q"],
    }
    task_path = _write_task(root, "task.json", task_data)
    result = check(root, task_path)
    assert result["state"] == "TASK_READY"
    dep_ctx = result["preparation_notes"]["dependency_context"]
    assert dep_ctx["persistence_contract_requested"] is True
    assert dep_ctx["missing_full_model_paths"] == []


def test_explicitly_named_repository_model_is_required_without_supplied_import(tmp_path: Path):
    root = _make_mock_root(tmp_path)
    service_file = root / "scripts/mock_service.py"
    service_file.parent.mkdir(parents=True, exist_ok=True)
    service_file.write_text("def persist_fixture():\n    return None\n", encoding="utf-8")

    model_file = root / "models/accountant_relation.py"
    model_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text("class AccountantRelation:\n    pass\n", encoding="utf-8")

    task_data = {
        "task_id": "task-explicit-model-without-import",
        "goal": "Persist an AccountantRelation database fixture in sqlite.",
        "context_files": ["scripts/mock_service.py"],
        "allowed_files": ["scripts/mock_service.py"],
        "requirements": ["Construct AccountantRelation and query the durable row."],
        "test_selectors": ["pytest -q"],
    }
    task_path = _write_task(root, "task.json", task_data)

    with pytest.raises(ProposalError, match="missing model context: models/accountant_relation.py"):
        check(root, task_path)


def test_unrelated_warnings_do_not_block_readiness():
    advisory_notes = {
        "schema_version": 1,
        "exact_blocker_ids": [],
        "warnings": ["MISSING_EXACT_BLOCKER_ID", "SOURCE_BLOCK_OMITTED", "BLOCKER_IDS_OMITTED"],
        "literal_hints": [],
        "omitted_literal_count": 0,
        "dependency_context": {
            "persistence_contract_requested": False,
            "required_full_model_paths": [],
            "missing_full_model_paths": [],
            "omitted_model_path_count": 0,
        },
        "output_contract": {
            "max_provider_output_tokens": 60000,
            "max_test_only_transport_bytes": 64000,
            "max_new_test_file_utf8_bytes": 24000,
            "test_only_task": False,
            "new_test_files": [],
        },
    }
    validated = validate_task_readiness(advisory_notes)
    assert validated == advisory_notes


def test_direct_run_cannot_bypass_readiness_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _make_mock_root(tmp_path)
    service_file = root / "scripts/mock_service.py"
    service_file.parent.mkdir(parents=True, exist_ok=True)
    service_file.write_text("from models.user import User\n", encoding="utf-8")

    task_data = {
        "task_id": "task-bypass-test",
        "goal": "Direct run check with sqlite table persistence for User.",
        "context_files": ["scripts/mock_service.py"],
        "allowed_files": ["scripts/mock_service.py"],
        "requirements": ["Save records."],
        "test_selectors": ["pytest -q"],
    }
    task_path = _write_task(root, "task.json", task_data)

    cli_probed = False

    def _trap_cli_probe(*args, **kwargs):
        nonlocal cli_probed
        cli_probed = True
        raise AssertionError("cli_identity or provider must not be reached")

    monkeypatch.setattr("scripts.two_site_proposal_runner.validate_runtime_contract", lambda r: {})
    monkeypatch.setattr("scripts.two_site_proposal_runner.cli_identity", _trap_cli_probe)

    with pytest.raises(ProposalError) as exc:
        run(root, task_path, "worker", "run-bypass-id", 60)
    assert "missing model context: models/user.py" in str(exc.value)
    assert not cli_probed


def test_schema_injects_max_length_for_new_test():
    task = {
        "task_id": "task-test-only",
        "goal": "Refactor unit tests only.",
        "context_files": [],
        "allowed_files": ["tests/test_unit_new.py"],
        "requirements": ["Add unit tests."],
        "test_selectors": ["pytest -q"],
    }
    sources = {
        "tests/test_unit_new.py": {"sha256": None, "content": None},
    }
    generated = schema("worker", task, sources)
    changes_schema = generated["properties"]["changes"]
    serialized = json.dumps(changes_schema)
    assert "maxLength" in serialized
    assert 24000 in [changes_schema["items"].get("properties", {}).get("content", {}).get("maxLength")] or "24000" in serialized


def test_validate_result_rejects_over_budget_new_test_file_utf8():
    task = {
        "task_id": "task-test-only",
        "goal": "Add unit tests only.",
        "context_files": [],
        "allowed_files": ["tests/test_unit_new.py"],
        "requirements": ["Add test."],
        "test_selectors": ["pytest -q"],
    }
    sources = {
        "tests/test_unit_new.py": {"sha256": None, "content": None},
    }

    over_ascii = "a" * 24001
    result_ascii = {
        "status": "PROPOSAL_READY",
        "summary": "Added new test.",
        "covered_requirements": [1],
        "findings": [],
        "changes": [{
            "path": "tests/test_unit_new.py",
            "original_sha256": None,
            "content": over_ascii,
        }],
    }
    with pytest.raises(ProposalError) as exc_ascii:
        validate_result(result_ascii, task, sources, "worker")
    assert "new test file content exceeds budget" in str(exc_ascii.value)

    multi_byte_char = "\u200c"  # 3 UTF-8 bytes each, 9000 chars = 27000 UTF-8 bytes
    over_utf8 = multi_byte_char * 9000
    assert len(over_utf8) < 24000
    assert len(over_utf8.encode("utf-8")) > 24000

    result_multibyte = {
        "status": "PROPOSAL_READY",
        "summary": "Added multi-byte test.",
        "covered_requirements": [1],
        "findings": [],
        "changes": [{
            "path": "tests/test_unit_new.py",
            "original_sha256": None,
            "content": over_utf8,
        }],
    }
    with pytest.raises(ProposalError) as exc_mb:
        validate_result(result_multibyte, task, sources, "worker")
    assert "new test file content exceeds budget" in str(exc_mb.value)


def test_validate_result_rejects_over_budget_test_only_raw_transport():
    task = {
        "task_id": "task-test-only",
        "goal": "Refactor tests only.",
        "context_files": [],
        "allowed_files": ["tests/test_unit_1.py", "tests/test_unit_2.py", "tests/test_unit_3.py"],
        "requirements": ["Add tests."],
        "test_selectors": ["pytest -q"],
    }
    sources = {
        "tests/test_unit_1.py": {"sha256": None, "content": None},
        "tests/test_unit_2.py": {"sha256": None, "content": None},
        "tests/test_unit_3.py": {"sha256": None, "content": None},
    }
    # 3 files * 22000 bytes = 66000 bytes > 64000 limit, each under 24000
    result = {
        "status": "PROPOSAL_READY",
        "summary": "Added three tests.",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {"path": "tests/test_unit_1.py", "original_sha256": None, "content": "x = 1\n" + ("#" * 21990)},
            {"path": "tests/test_unit_2.py", "original_sha256": None, "content": "y = 2\n" + ("#" * 21990)},
            {"path": "tests/test_unit_3.py", "original_sha256": None, "content": "z = 3\n" + ("#" * 21990)},
        ],
    }
    with pytest.raises(ProposalError) as exc:
        validate_result(result, task, sources, "worker")
    assert "test-only raw transport exceeds budget" in str(exc.value)


def test_non_test_tasks_retain_existing_ceilings():
    task = {
        "task_id": "task-code-edit",
        "goal": "Update implementation script.",
        "context_files": [],
        "allowed_files": ["scripts/worker_script.py"],
        "requirements": ["Update script."],
        "test_selectors": ["pytest -q"],
    }
    sources = {
        "scripts/worker_script.py": {"sha256": None, "content": None},
    }
    # 70000 bytes > 64000 test-only limit, but < 160000 normal context limit
    content = "# " + ("a" * 70000) + "\n"
    result = {
        "status": "PROPOSAL_READY",
        "summary": "Updated implementation script.",
        "covered_requirements": [1],
        "findings": [],
        "changes": [{
            "path": "scripts/worker_script.py",
            "original_sha256": None,
            "content": content,
        }],
    }
    # Must NOT fail with test-only budget error
    validated = validate_result(result, task, sources, "worker")
    assert validated["status"] == "PROPOSAL_READY"


def test_parse_stream_rejects_error_model_output_truncated_with_structured_output():
    task = {
        "task_id": "task-truncation",
        "goal": "Check truncation rejection.",
        "context_files": [],
        "allowed_files": ["scripts/app.py"],
        "requirements": ["Do task."],
        "test_selectors": ["pytest -q"],
    }
    sources = {
        "scripts/app.py": {"sha256": None, "content": None},
    }
    fake_structured_output = {
        "status": "PROPOSAL_READY",
        "summary": "Incomplete partial output.",
        "covered_requirements": [1],
        "findings": [],
        "changes": [{
            "path": "scripts/app.py",
            "original_sha256": None,
            "content": "print('hello')\n",
        }],
    }
    stream_lines = [
        json.dumps({"event": "init", "conversation_id": "test-session", "init": {"model": "gemini-2.5-pro"}}),
        json.dumps({
            "event": "result",
            "conversation_id": "test-session",
            "result": {
                "conversation_id": "test-session",
                "status": "ERROR",
                "error": "Agent execution terminated due to MODEL_OUTPUT_TRUNCATED: output tokens limit exceeded",
                "structured_output": fake_structured_output,
            },
        }),
    ]
    raw_stream = "\n".join(stream_lines) + "\n"
    with pytest.raises(ProposalError) as exc:
        _parse_stream(raw_stream, task, sources, "worker", expected_model="gemini-2.5-pro")
    assert "MODEL_OUTPUT_TRUNCATED" in str(exc.value)
