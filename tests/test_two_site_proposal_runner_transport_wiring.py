"""Integration tests for two-site proposal runner transport wiring."""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from unittest.mock import patch
import pytest

try:
    import scripts.two_site_proposal_runner as runner
    from scripts.two_site_proposal_runner import (
        MAX_REPLACEMENTS,
        ProposalError,
        normalize_result,
        parse_stream,
        run,
        schema,
    )
    from scripts.two_site_proposal_transport import (
        FULL_CONTENT_BYTE_THRESHOLD,
        TransportPolicyError,
    )
except ImportError:
    import two_site_proposal_runner as runner
    from two_site_proposal_runner import (
        MAX_REPLACEMENTS,
        ProposalError,
        normalize_result,
        parse_stream,
        run,
        schema,
    )
    from two_site_proposal_transport import (
        FULL_CONTENT_BYTE_THRESHOLD,
        TransportPolicyError,
    )

LARGE_TEXT = "x = 1\n" + ("# filler line for large fixture threshold test\n" * 450)
assert len(LARGE_TEXT.encode("utf-8")) >= FULL_CONTENT_BYTE_THRESHOLD
SMALL_TEXT = "s = 1\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_task() -> dict:
    return {
        "task_id": "transport-wiring-test",
        "goal": "Test transport wiring",
        "context_files": ["file_new.py", "file_small.py", "file_large.py"],
        "allowed_files": ["file_new.py", "file_small.py", "file_large.py"],
        "requirements": ["req1"],
        "test_selectors": ["pytest"],
    }


def make_sources() -> dict:
    return {
        "file_new.py": {"sha256": None, "content": None},
        "file_small.py": {"sha256": sha(SMALL_TEXT), "content": SMALL_TEXT},
        "file_large.py": {"sha256": sha(LARGE_TEXT), "content": LARGE_TEXT},
    }


def make_proposal(changes: list) -> dict:
    return {
        "status": "PROPOSAL_READY",
        "summary": "Implementation summary for transport wiring test",
        "covered_requirements": [1],
        "findings": [],
        "changes": changes,
    }


def make_stream(prop: dict, conversation_id: str = "test-conv-1") -> str:
    events = [
        {"event": "init", "conversation_id": conversation_id, "init": {"model": runner.MODEL}},
        {"event": "result", "conversation_id": conversation_id, "result": {
            "status": "SUCCESS", "conversation_id": conversation_id, "structured_output": prop,
        }},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\n"


def test_large_source_threshold_explicit():
    utf8_bytes = LARGE_TEXT.encode("utf-8")
    assert len(utf8_bytes) >= FULL_CONTENT_BYTE_THRESHOLD


def test_schema_generic_and_aware():
    worker_gen = schema("worker")
    assert "changes" in worker_gen["properties"]
    assert worker_gen["properties"]["status"]["enum"] == ["PROPOSAL_READY", "BLOCKED"]

    verifier_gen = schema("verifier")
    assert verifier_gen["properties"]["status"]["enum"] == [
        "VERIFIED", "CHANGES_REQUIRED", "BLOCKED"
    ]

    task = make_task()
    sources = make_sources()
    with pytest.raises(ProposalError, match="task and sources"):
        schema("worker", task=task)
    with pytest.raises(ProposalError, match="task and sources"):
        schema("worker", sources=sources)

    worker_aware = schema("worker", task=task, sources=sources)
    branches = worker_aware["properties"]["changes"]["items"]["anyOf"]
    assert len(branches) == 4

    verifier_aware = schema("verifier", task=task, sources=sources)
    assert verifier_aware["properties"]["changes"] == {
        "type": "array",
        "maxItems": 0,
        "items": {"type": "object", "additionalProperties": False},
    }


def test_schema_branches_new_large_small_verifier():
    task = make_task()
    sources = make_sources()
    s = schema("worker", task=task, sources=sources)
    branches = s["properties"]["changes"]["items"]["anyOf"]

    new_branches = [b for b in branches if b["properties"]["path"]["enum"] == ["file_new.py"]]
    assert len(new_branches) == 1
    assert new_branches[0]["properties"]["content"]["type"] == "string"
    assert new_branches[0]["properties"]["replacements"]["maxItems"] == 0

    large_branches = [b for b in branches if b["properties"]["path"]["enum"] == ["file_large.py"]]
    assert len(large_branches) == 1
    assert large_branches[0]["properties"]["content"]["type"] == "null"
    assert large_branches[0]["properties"]["replacements"]["minItems"] == 1
    assert large_branches[0]["properties"]["replacements"]["maxItems"] == MAX_REPLACEMENTS

    small_branches = [b for b in branches if b["properties"]["path"]["enum"] == ["file_small.py"]]
    assert len(small_branches) == 2
    kinds = {b["properties"]["content"]["type"] for b in small_branches}
    assert kinds == {"string", "null"}

    v = schema("verifier", task=task, sources=sources)
    assert v["properties"]["changes"]["maxItems"] == 0


@pytest.mark.parametrize("bad_flag", [1, 0, "true", "false", None, [], {}])
def test_normalize_result_strict_bool_validation(bad_flag):
    task = make_task()
    sources = make_sources()
    prop = make_proposal([])
    with pytest.raises(ProposalError, match="strict_transport must be a boolean"):
        normalize_result(prop, task, sources, "worker", strict_transport=bad_flag)


def test_normalize_result_direct_legacy_compatibility():
    task = make_task()
    sources = make_sources()
    legacy_change = {
        "path": "file_large.py",
        "original_sha256": sources["file_large.py"]["sha256"],
        "content": "legacy direct content without replacements",
    }
    prop = make_proposal([legacy_change])
    norm = normalize_result(prop, task, sources, "worker")
    assert norm["changes"][0]["content"] == "legacy direct content without replacements"


def test_forced_correction_path_rejects_legacy_three_key_transport():
    task = make_task()
    sources = make_sources()
    legacy_change = {
        "path": "file_small.py",
        "original_sha256": sources["file_small.py"]["sha256"],
        "content": "changed complete content",
    }
    with pytest.raises(ProposalError, match="proposal transport policy violation"):
        normalize_result(
            make_proposal([legacy_change]), task, sources, "worker",
            strict_transport=True, force_full_paths=("file_small.py",),
        )


def test_normalize_result_strict_rejection_and_error_translation():
    task = make_task()
    sources = make_sources()
    bad_change = {
        "path": "file_large.py",
        "original_sha256": sources["file_large.py"]["sha256"],
        "content": "disallowed full content on large file",
        "replacements": [],
    }
    prop = make_proposal([bad_change])
    with pytest.raises(ProposalError) as exc_info:
        normalize_result(prop, task, sources, "worker", strict_transport=True)
    assert not isinstance(exc_info.value, TransportPolicyError)
    err_str = str(exc_info.value)
    assert LARGE_TEXT not in err_str
    assert "disallowed full content" not in err_str


def test_normalize_result_valid_large_replacement_materialization():
    task = make_task()
    sources = make_sources()
    valid_change = {
        "path": "file_large.py",
        "original_sha256": sources["file_large.py"]["sha256"],
        "content": None,
        "replacements": [{"old": "x = 1\n", "new": "x = 42\n"}],
    }
    prop = make_proposal([valid_change])
    norm = normalize_result(prop, task, sources, "worker", strict_transport=True)
    materialized = norm["changes"][0]["content"]
    assert materialized.startswith("x = 42\n")
    assert len(materialized.encode("utf-8")) >= FULL_CONTENT_BYTE_THRESHOLD


def test_parse_stream_no_bypass_and_strict_live_rejection():
    sig = inspect.signature(parse_stream)
    assert list(sig.parameters.keys()) == ["raw", "task", "sources", "role"]

    task = make_task()
    sources = make_sources()

    bad_prop = make_proposal([{
        "path": "file_large.py",
        "original_sha256": sources["file_large.py"]["sha256"],
        "content": "full content disallowed on large file in strict live mode",
        "replacements": [],
    }])
    with pytest.raises(ProposalError):
        parse_stream(make_stream(bad_prop), task, sources, "worker")

    good_prop = make_proposal([{
        "path": "file_large.py",
        "original_sha256": sources["file_large.py"]["sha256"],
        "content": None,
        "replacements": [{"old": "x = 1\n", "new": "x = 99\n"}],
    }])
    result, envelope = parse_stream(make_stream(good_prop), task, sources, "worker")
    assert result["changes"][0]["content"].startswith("x = 99\n")
    assert envelope["status"] == "SUCCESS"


def test_parse_stream_canonical_four_key_transport():
    task = make_task()
    sources = make_sources()
    prop = make_proposal([{
        "path": "file_new.py",
        "original_sha256": None,
        "content": "print('canonical stream content')\n",
        "replacements": [],
    }])
    result, envelope = parse_stream(make_stream(prop), task, sources, "worker")
    assert result["changes"][0]["path"] == "file_new.py"
    assert result["changes"][0]["content"] == "print('canonical stream content')\n"
    assert envelope["status"] == "SUCCESS"


def test_run_command_uses_task_aware_schema(tmp_path: Path):
    task = make_task()
    sources = make_sources()
    task_file = tmp_path / "task.json"
    task_file.write_text(json.dumps(task))
    cli_path = tmp_path / "mock_cli"
    cli_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cli_path.chmod(0o755)

    fixture_cid = "conv-test-schema-001"
    captured_cmd = None
    captured_cwd = None

    class FakePopen:
        def __init__(self, cmd, cwd=None, **kwargs):
            nonlocal captured_cmd, captured_cwd
            captured_cmd = list(cmd)
            captured_cwd = Path(cwd) if cwd is not None else None
            raise RuntimeError("intercepted_command")

    def mock_prepare_profile(disposable_cli, workspace, model, effort=None, **kwargs):
        return runner.PreparedProfile(
            conversation_id=fixture_cid,
            workspace=Path(workspace),
            model=model,
            effort=effort,
        )

    def mock_validate_profile(profile, fresh=True):
        return {"profile": "valid", "fresh": fresh}

    with patch.object(runner, "_generation_contract_sha", return_value="0" * 64), \
         patch.object(runner, "load_task", return_value=(task, sources)), \
         patch.object(runner, "git", side_effect=lambda *args: "HEAD_SHA" if "rev-parse" in args else ""), \
         patch.object(runner, "bounded_file_text", return_value="template prompt"), \
         patch.object(runner, "proposal_cli_context") as mock_ctx, \
         patch.object(runner, "cli_identity", return_value={"path": str(cli_path), "version": "1.0", "sha256": "abc"}), \
         patch.object(runner, "prepare_profile", side_effect=mock_prepare_profile), \
         patch.object(runner, "validate_profile", side_effect=mock_validate_profile), \
         patch.object(runner.subprocess, "Popen", side_effect=FakePopen):

        mock_ctx.return_value.__enter__.return_value = (tmp_path, cli_path, {"path": str(cli_path)})
        mock_ctx.return_value.__exit__.return_value = False

        with pytest.raises(RuntimeError, match="intercepted_command"):
            run(tmp_path, task_file, "worker", "run-1", 100)

    assert captured_cmd is not None
    assert captured_cmd[0] == str(cli_path)
    assert captured_cmd.count("--json-schema") == 1
    assert captured_cmd.count("--conversation") == 1

    conv_idx = captured_cmd.index("--conversation")
    assert captured_cmd[conv_idx + 1] == fixture_cid
    expected_workspace = tmp_path / "proposal-runtime-workspace"
    assert captured_cwd == expected_workspace
    assert captured_cwd.is_dir()
    assert (captured_cwd / ".agents" / "hooks.json").is_file()

    idx = captured_cmd.index("--json-schema")
    schema_file_path = Path(captured_cmd[idx + 1])
    expected_schema_path = tmp_path / "run-1" / "response-schema.json"
    assert schema_file_path == expected_schema_path
    assert schema_file_path.is_file()

    schema_bytes = schema_file_path.read_bytes()
    actual_schema = json.loads(schema_bytes.decode("utf-8"))
    expected_schema = schema("worker", task, sources)
    assert schema_bytes == runner.serialize_json(expected_schema)
    assert actual_schema == expected_schema
    assert actual_schema != schema("worker")
