"""Synthetic telemetry contracts; never provider or billing evidence."""
import json
import os
from pathlib import Path

import pytest

import hashlib

from scripts import two_site_provider_metrics as metrics
from scripts import two_site_cycle_metrics as ledger
try:
    from scripts import two_site_proposal_runner as runner
except ModuleNotFoundError:
    import two_site_proposal_runner as runner
try:
    from scripts.two_site_pre_review_receipt import validate_test_receipt
except ModuleNotFoundError:
    from two_site_pre_review_receipt import validate_test_receipt
try:
    from scripts.two_site_semantic_task import build_identity, validate_identity
except ModuleNotFoundError:
    from two_site_semantic_task import build_identity, validate_identity

HEAD = "a" * 40
TOKENS = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14, "thinking_tokens": 3}


def attempt(root, suffix, **overrides):
    name = "unit-" + suffix
    path = root / ".local/two-site-refactor/proposals" / name
    path.mkdir(parents=True)
    reviewer = "reviewer" in suffix
    value = {"run_id": name, "task_id": "task", "head": HEAD,
             "role": "verifier" if reviewer else "worker",
             "state": "VERIFIED" if reviewer else "PROPOSAL_READY",
             "usage": TOKENS, "duration_seconds": 5}
    value.update(overrides)
    if reviewer:
        value.setdefault("input_worker_run_id", "unit-worker-0")
    (path / "status.json").write_text(json.dumps(value))
    return path


def pair(root):
    return attempt(root, "worker-0"), attempt(root, "reviewer-0")


def read(root, final="unit-worker-0", duration=50):
    return metrics.provider_metrics(root, "task", HEAD, final, duration)


def test_first_pass_complete_bounded_deterministic_readonly(tmp_path):
    pair(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    result = read(tmp_path)
    assert result == read(tmp_path) and result["first_pass"] is True
    assert result["usage"] == {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}
    assert result["phase_seconds"] == {"provider_reported": 10, "outside_provider_unattributed": 40}
    assert result["successful_correction_rounds"] == 0
    assert len(json.dumps(result).encode()) < 4000
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}


@pytest.mark.parametrize("suffix", ["codex-worker-1", "codex-correction-1-worker"])
def test_corrections_and_failed_retry_recovered_once(tmp_path, suffix):
    pair(tmp_path)
    failed = attempt(tmp_path, "worker-1", state="BLOCKED", usage=None)
    event = {"event": "result", "result": {"usage": TOKENS, "response": "DO_NOT_LEAK"}}
    (failed / "stream.jsonl").write_text(json.dumps(event) + "\n")
    attempt(tmp_path, suffix, input_proposal_sha256="b" * 64)
    attempt(tmp_path, "reviewer-1", input_worker_run_id="unit-" + suffix)
    result = read(tmp_path, "unit-" + suffix)
    assert result["first_pass"] is False and result["failed_attempts"] == 1
    assert result["successful_correction_rounds"] == 1 and result["attempts"] == 5
    assert result["usage"]["total_tokens"] == 70
    assert "DO_NOT_LEAK" not in json.dumps(result)


@pytest.mark.parametrize("raw", [None, "{\n", "{}", "{}\n", "[]\n", "x" * 2_000_001,
    json.dumps({"event": "result", "result": {"usage": TOKENS}}) + "\n" +
    json.dumps({"event": "result", "result": {"usage": TOKENS}}) + "\n"])
def test_missing_malformed_oversized_duplicate_stream_unknown(tmp_path, raw):
    pair(tmp_path)
    failed = attempt(tmp_path, "worker-1", state="BLOCKED", usage=None)
    if raw is not None:
        (failed / "stream.jsonl").write_text(raw)
    result = read(tmp_path)
    assert result["usage"]["total_tokens"] is None
    assert result["observed_usage_subtotal"]["total_tokens"] == 28


@pytest.mark.parametrize("changes", [{"head": "b" * 40}, {"task_id": "other"},
    {"run_id": "alias"}, {"state": "RUNNING"}, {"role": "fake"}])
def test_mismatch_is_partial_not_false_zero(tmp_path, changes):
    pair(tmp_path)
    attempt(tmp_path, "worker-1", **changes)
    result = read(tmp_path)
    assert result["state"] == "PARTIAL" and result["first_pass"] is None
    assert result["usage"]["total_tokens"] is None


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_aliases_rejected(tmp_path, kind):
    worker, _ = pair(tmp_path)
    path = worker / "status.json"
    other = tmp_path / "original.json"
    path.rename(other)
    if kind == "symlink":
        path.symlink_to(other)
    else:
        os.link(other, path)
    assert read(tmp_path)["first_pass"] is None


@pytest.mark.parametrize("number", [-1, True, float("nan"), float("inf")])
def test_bad_numbers_unknown(tmp_path, number):
    attempt(tmp_path, "worker-0", usage={**TOKENS, "input_tokens": number}, duration_seconds=number)
    attempt(tmp_path, "reviewer-0")
    result = read(tmp_path)
    assert result["usage"]["total_tokens"] is None
    assert result["phase_seconds"]["provider_reported"] is None


def test_limits_legacy_missing_and_impossible_duration(tmp_path):
    assert read(tmp_path)["state"] == "UNAVAILABLE"
    pair(tmp_path)
    assert read(tmp_path, "legacy-final")["state"] == "UNAVAILABLE"
    assert read(tmp_path, duration=1)["timing_coverage"] == "UNKNOWN"
    for n in range(1, 32):
        attempt(tmp_path, "worker-" + str(n))
    assert read(tmp_path)["state"] == "UNAVAILABLE"


def _make_runner_materials():
    gap_ref = "docs/refactor/two-server/AUTOMATION_EFFICIENCY_PLAN.md#7-بازیابی-هدفمند"
    stage_id = "P1-00b"
    scenario_key = "ae03.provider-metrics.bound-canonical-result-20260928-059"
    source_path = "pkg/module.py"

    source_content = (
        "def compute_value():\n"
        + ("    # " + ("x" * 70) + "\n") * 1950
        + "    return 1\n"
    )
    source_bytes = source_content.encode("utf-8")
    assert len(source_bytes) <= runner.MAX_CONTEXT
    source_sha = hashlib.sha256(source_bytes).hexdigest()

    sources = {
        source_path: {
            "sha256": source_sha,
            "content": source_content,
        }
    }

    identity = build_identity(
        stage_id=stage_id,
        gap_reference=gap_ref,
        scenario_key=scenario_key,
        symbol_groups=[{"path": source_path, "symbols": ["compute_value"]}],
        source_manifest={source_path: source_sha},
    )
    assert validate_identity(identity) == identity

    goal = (
        "One bounded task.\nCANONICAL_SOURCE_EXCERPTS_JSON:\n"
        + json.dumps({"gap_reference": gap_ref, "semantic_identity": identity}, ensure_ascii=False)
    )
    task = {
        "task_id": "task",
        "goal": goal,
        "requirements": ["R1: reader bound"],
        "test_selectors": ["tests/test_mod.py"],
        "context_files": [source_path],
        "allowed_files": [source_path],
    }

    base_bytes = json.dumps({"task": task, "sources": sources}, ensure_ascii=False).encode("utf-8")
    assert len(base_bytes) <= runner.MAX_CONTEXT

    candidate_content = (
        "def compute_value():\n"
        + ("    # " + ("x" * 70) + "\n") * 1950
        + "    return 2\n"
    )
    candidate = {
        "status": "PROPOSAL_READY",
        "summary": "Fix compute_value return value.",
        "covered_requirements": [1],
        "findings": [],
        "changes": [
            {
                "path": source_path,
                "original_sha256": source_sha,
                "content": candidate_content,
            }
        ],
    }
    validated_candidate = runner.validate_result(candidate, task, sources, role="worker")
    assert validated_candidate == candidate

    proposal_bytes = runner.serialize_json(candidate)
    proposal_text = proposal_bytes.decode("utf-8")
    assert len(proposal_bytes) <= runner.MAX_CONTEXT
    proposal_sha256 = hashlib.sha256(proposal_bytes).hexdigest()

    review_transport = runner.build_review_transport(candidate, sources, proposal_sha256)
    assert review_transport["canonical_proposal_sha256"] == proposal_sha256
    assert len(runner.serialize_json(review_transport)) <= 64_000

    raw_receipt = {
        "state": "PASSED",
        "proposal_sha256": proposal_sha256,
        "selectors": ["tests/test_mod.py"],
        "unsupported_specs": [],
        "returncode": 0,
        "stdout_tail": "o" * 8192,
        "stderr_tail": "e" * 8192,
        "snapshot_files": 1,
        "snapshot_bytes": 100,
        "isolation": "hermetic",
    }
    normalized_receipt = validate_test_receipt(raw_receipt, proposal_sha256, require_passed=True)

    worker_material = {
        "task": task,
        "source_head": HEAD,
        "sources": sources,
    }
    reviewer_material = {
        "task": task,
        "source_head": HEAD,
        "sources": sources,
        "proposal": proposal_text,
        "test_evidence": normalized_receipt,
    }

    runner._validate_task_snapshot_budget(worker_material)
    runner._validate_task_snapshot_budget(reviewer_material)

    worker_bytes = runner.serialize_json(worker_material)
    reviewer_bytes = runner.serialize_json(reviewer_material)

    assert len(worker_bytes) <= 3 * runner.MAX_CONTEXT
    assert 2 * runner.MAX_CONTEXT < len(reviewer_bytes) <= 3 * runner.MAX_CONTEXT

    return gap_ref, identity, worker_material, reviewer_material


def test_task_metadata_accepts_runner_valid_snapshot_and_canonical_excerpt_limits(tmp_path):
    worker, reviewer = pair(tmp_path)
    gap_ref, identity, worker_mat, reviewer_mat = _make_runner_materials()

    (worker / "task.json").write_bytes(runner.serialize_json(worker_mat))
    (reviewer / "task.json").write_bytes(runner.serialize_json(reviewer_mat))

    result = read(tmp_path)
    assert result["state"] == "OBSERVED_MATCHING_ATTEMPTS"
    assert result["task_metadata"] is not None
    assert result["task_metadata"]["task_id"] == "task"
    assert result["task_metadata"]["declared_requirement_count"] == 1
    assert result["task_metadata"]["focused_selector_count"] == 1
    assert result["task_metadata"]["gap_reference"] == gap_ref
    assert result["semantic_identity"] is not None
    assert result["semantic_identity"]["gap_reference"] == gap_ref
    assert result["semantic_identity"]["fingerprint"] == identity["fingerprint"]
    assert result["semantic_identity"]["stage_id"] == identity["stage_id"]
    assert result["semantic_identity"]["scenario_key"] == identity["scenario_key"]
    assert result["semantic_identity"]["source_sha256"] == identity["source_sha256"]


@pytest.mark.parametrize("scenario", ["oversized", "head_mismatch"])
def test_task_metadata_rejects_oversized_snapshot_and_head_mismatch(tmp_path, scenario):
    worker, reviewer = pair(tmp_path)
    _, _, worker_mat, reviewer_mat = _make_runner_materials()

    (worker / "task.json").write_bytes(runner.serialize_json(worker_mat))

    if scenario == "oversized":
        oversized = dict(reviewer_mat)
        current_len = len(runner.serialize_json(reviewer_mat))
        pad_needed = (3 * runner.MAX_CONTEXT - current_len) + 4096
        oversized["proposal"] = reviewer_mat["proposal"] + (" " * pad_needed)
        oversized_bytes = runner.serialize_json(oversized)
        assert len(oversized_bytes) > 3 * runner.MAX_CONTEXT
        (reviewer / "task.json").write_bytes(oversized_bytes)
    else:
        mismatched = dict(reviewer_mat, source_head="b" * 40)
        (reviewer / "task.json").write_bytes(runner.serialize_json(mismatched))

    result = read(tmp_path)
    assert result["task_metadata"] is None
    assert result["semantic_identity"] is None


@pytest.mark.parametrize("failure", [False, True])
def test_ledger_binding_counts_and_helper_failure(tmp_path, monkeypatch, failure):
    worker, _ = pair(tmp_path)
    (worker / "task.json").write_text(json.dumps({"source_head": HEAD, "task": {
        "task_id": "task", "goal": "one scope", "requirements": ["a", "b"], "test_selectors": ["test"]}}))
    state = tmp_path / ".local/two-site-refactor/autopilot"
    run = state / "runs/one"
    run.mkdir(parents=True)
    (run / "stream.jsonl").write_text("{}\n")
    (run / "prompt.txt").write_text("DO_NOT_LEAK")
    if failure:
        def fail(*args):
            raise ValueError("DO_NOT_LEAK")
        monkeypatch.setattr(metrics, "provider_metrics", fail)
    result = ledger.record_cycle(tmp_path, state, run, {"state": "CONTINUE", "head_before": HEAD,
        "gemini_worker_run_id": "unit-worker-0", "duration_seconds": 50}, "sol", "xhigh")
    assert result["state"] == "CONTINUE" and result["declared_requirement_count"] == 2
    assert result["focused_selector_count"] == 1
    assert result["provider_metrics"]["state"] == ("UNAVAILABLE" if failure else "OBSERVED_MATCHING_ATTEMPTS")
    assert json.loads((run / "metrics.json").read_text()) == result
    assert "DO_NOT_LEAK" not in json.dumps(result)
