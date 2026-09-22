"""Hermetic tests for canonical budget pipeline validation and raw cap separation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

try:
    from scripts.two_site_proposal_pipeline import validate_run
    from scripts.two_site_proposal_runner import (
        MAX_CONTEXT,
        WORKER_MODEL,
        ProposalError,
        _parse_stream,
        file_sha256,
        serialize_json,
        validate_result,
        write_json,
    )
except ModuleNotFoundError:
    from two_site_proposal_pipeline import validate_run  # type: ignore[no-redef]
    from two_site_proposal_runner import (  # type: ignore[no-redef]
        MAX_CONTEXT,
        WORKER_MODEL,
        ProposalError,
        _parse_stream,
        file_sha256,
        serialize_json,
        validate_result,
        write_json,
    )


def _make_test_fixture(tmp_path: Path) -> dict:
    root = tmp_path.resolve()
    target_path = "tests/test_canonical_sample.py"
    base_content = (
        '"""' + ("A" * 70000) + '"""\n'
        "# anchor\n"
        "def test_sample():\n"
        "    assert True\n"
    )
    base_sha = hashlib.sha256(base_content.encode("utf-8")).hexdigest()

    sources = {
        target_path: {
            "sha256": base_sha,
            "content": base_content,
        }
    }
    task = {
        "task_id": "TEST-CANONICAL-BUDGET-001",
        "requirements": ["canonical budget separation"],
        "allowed_files": [target_path],
        "preparation_notes": {
            "output_contract": {
                "test_only_task": True,
                "max_test_only_transport_bytes": 64000,
                "max_new_test_file_utf8_bytes": 24000,
                "new_test_files": [],
            }
        },
    }
    raw_replacement = {
        "status": "PROPOSAL_READY",
        "summary": "Compact raw replacement test",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {
                "path": target_path,
                "original_sha256": base_sha,
                "content": None,
                "replacements": [
                    {
                        "old": "# anchor\n",
                        "new": "# updated anchor\n",
                    }
                ],
            }
        ],
    }
    conv_id = "test-canonical-conv-001"
    stream_raw = (
        json.dumps({
            "event": "init",
            "conversation_id": conv_id,
            "init": {"model": WORKER_MODEL},
        })
        + "\n"
        + json.dumps({
            "event": "result",
            "conversation_id": conv_id,
            "result": {
                "conversation_id": conv_id,
                "status": "SUCCESS",
                "structured_output": raw_replacement,
            },
        })
        + "\n"
    )
    parsed_result, _ = _parse_stream(
        stream_raw, task, sources, "worker", expected_model=WORKER_MODEL
    )

    run_dir = root / "runs" / "run_worker_0"
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / "result.json"
    write_json(result_path, parsed_result)
    result_sha = file_sha256(result_path)

    head = "11223344556677889900aabbccddeeff00112233"
    run_id = "run-worker-0"
    status_payload = {
        "run_id": run_id,
        "role": "worker",
        "state": "PROPOSAL_READY",
        "head": head,
        "model": WORKER_MODEL,
        "result_sha256": result_sha,
        "conversation_id": conv_id,
    }
    write_json(run_dir / "status.json", status_payload)

    task_payload = {
        "source_head": head,
        "task": task,
        "sources": sources,
    }
    write_json(run_dir / "task.json", task_payload)

    return {
        "root": root,
        "run_dir": run_dir,
        "run_id": run_id,
        "head": head,
        "task": task,
        "sources": sources,
        "parsed_result": parsed_result,
        "result_sha": result_sha,
        "conv_id": conv_id,
        "status_payload": status_payload,
        "task_payload": task_payload,
        "target_path": target_path,
    }


def test_canonical_result_avoids_raw_cap_and_accepts(tmp_path: Path) -> None:
    f = _make_test_fixture(tmp_path)
    canonical_bytes = serialize_json(f["parsed_result"])
    content_bytes = f["parsed_result"]["changes"][0]["content"].encode("utf-8")

    assert 64000 < len(content_bytes) < MAX_CONTEXT
    assert 64000 < len(canonical_bytes) < MAX_CONTEXT

    with pytest.raises(ProposalError, match="test-only raw transport exceeds budget"):
        validate_result(f["parsed_result"], f["task"], f["sources"], "worker")

    seen_convs: set[str] = set()
    result, status, result_sha, result_text = validate_run(
        f["root"],
        f["run_dir"],
        "worker",
        f["run_id"],
        f["head"],
        f["task"],
        f["sources"],
        seen_convs,
    )
    assert result == f["parsed_result"]
    assert status["run_id"] == f["run_id"]
    assert result_sha == f["result_sha"]
    assert f["conv_id"] in seen_convs
    assert len(result_text.encode("utf-8")) == len(canonical_bytes)


def test_independent_mismatch_and_reused_conversation_rejections(tmp_path: Path) -> None:
    f = _make_test_fixture(tmp_path)
    run_dir = f["run_dir"]

    write_json(run_dir / "status.json", dict(f["status_payload"], result_sha256="0" * 64))
    with pytest.raises(ProposalError, match="metadata or snapshot mismatch"):
        validate_run(
            f["root"], run_dir, "worker", f["run_id"], f["head"], f["task"], f["sources"], set()
        )
    write_json(run_dir / "status.json", f["status_payload"])

    write_json(run_dir / "status.json", dict(f["status_payload"], head="9" * 40))
    with pytest.raises(ProposalError, match="metadata or snapshot mismatch"):
        validate_run(
            f["root"], run_dir, "worker", f["run_id"], f["head"], f["task"], f["sources"], set()
        )
    write_json(run_dir / "status.json", f["status_payload"])

    write_json(run_dir / "task.json", dict(f["task_payload"], source_head="9" * 40))
    with pytest.raises(ProposalError, match="metadata or snapshot mismatch"):
        validate_run(
            f["root"], run_dir, "worker", f["run_id"], f["head"], f["task"], f["sources"], set()
        )
    write_json(run_dir / "task.json", f["task_payload"])

    altered_task = dict(f["task"], task_id="OTHER-TASK")
    write_json(run_dir / "task.json", dict(f["task_payload"], task=altered_task))
    with pytest.raises(ProposalError, match="metadata or snapshot mismatch"):
        validate_run(
            f["root"], run_dir, "worker", f["run_id"], f["head"], f["task"], f["sources"], set()
        )
    write_json(run_dir / "task.json", f["task_payload"])

    altered_sources = dict(f["sources"], other={"sha256": "abc", "content": "xyz"})
    write_json(run_dir / "task.json", dict(f["task_payload"], sources=altered_sources))
    with pytest.raises(ProposalError, match="metadata or snapshot mismatch"):
        validate_run(
            f["root"], run_dir, "worker", f["run_id"], f["head"], f["task"], f["sources"], set()
        )
    write_json(run_dir / "task.json", f["task_payload"])

    with pytest.raises(ProposalError, match="reused conversation_id"):
        validate_run(
            f["root"], run_dir, "worker", f["run_id"], f["head"], f["task"], f["sources"], {f["conv_id"]}
        )


def test_oversized_canonical_result_and_new_file_cap_rejections(tmp_path: Path) -> None:
    f = _make_test_fixture(tmp_path)
    root = f["root"]

    oversized_dir = root / "runs" / "oversized_run"
    oversized_dir.mkdir(parents=True, exist_ok=True)
    write_json(oversized_dir / "status.json", f["status_payload"])
    write_json(oversized_dir / "task.json", f["task_payload"])
    (oversized_dir / "result.json").write_bytes(b" " * (MAX_CONTEXT + 1))

    with pytest.raises(ProposalError, match="worker result exceeds the safe context budget"):
        validate_run(
            root, oversized_dir, "worker", f["run_id"], f["head"], f["task"], f["sources"], set()
        )

    new_test_path = "tests/test_new_component.py"
    new_task = dict(f["task"])
    new_task["allowed_files"] = [new_test_path]
    new_task["preparation_notes"] = {
        "output_contract": {
            "test_only_task": True,
            "max_test_only_transport_bytes": 64000,
            "max_new_test_file_utf8_bytes": 24000,
            "new_test_files": [new_test_path],
        }
    }
    new_sources = {
        new_test_path: {
            "sha256": None,
            "content": None,
        }
    }
    oversized_new_content = (
        '"""' + ("B" * 25000) + '"""\n'
        "def test_new():\n"
        "    assert True\n"
    )
    new_file_result = {
        "status": "PROPOSAL_READY",
        "summary": "Oversized new test file proposal",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {
                "path": new_test_path,
                "original_sha256": None,
                "content": oversized_new_content,
            }
        ],
    }
    new_dir = root / "runs" / "new_file_run"
    new_dir.mkdir(parents=True, exist_ok=True)
    write_json(new_dir / "result.json", new_file_result)
    new_result_sha = file_sha256(new_dir / "result.json")

    new_status = dict(f["status_payload"], result_sha256=new_result_sha)
    write_json(new_dir / "status.json", new_status)
    new_snapshot = {
        "source_head": f["head"],
        "task": new_task,
        "sources": new_sources,
    }
    write_json(new_dir / "task.json", new_snapshot)

    with pytest.raises(ProposalError, match="new test file content exceeds budget"):
        validate_run(
            root, new_dir, "worker", f["run_id"], f["head"], new_task, new_sources, set()
        )
