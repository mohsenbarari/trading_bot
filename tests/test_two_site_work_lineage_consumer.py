"""Tests for two-site work lineage consumer and record_cycle observer."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import pytest

from scripts import two_site_cycle_metrics as metrics
from scripts.two_site_proposal_runner import (
    ProposalError, bounded_file_text, unique_json,
)
from scripts.two_site_semantic_task import build_identity

FROZEN_WORK_PAYLOAD = {
    "gap_reference": "docs/ref#semantic",
    "kind": "two-site-logical-work",
    "scenario_key": "ae00_provider_identity_tests",
    "schema_version": 1,
    "stage_id": "P1-00b",
    "symbol_groups": [
        {"path": "scripts/two_site_provider_metrics.py", "symbols": ["provider_metrics"]}
    ],
}
FROZEN_WORK_SHA = "41de8c6f7d2f61cc10a9a71beb9ef4d33e06b52e6a3dfdffd02266d2a32c10e2"

FROZEN_PROJECTION_A = {
    "stage_id": "P1-00b",
    "scenario_key": "ae00_provider_identity_tests",
    "gap_reference": "docs/ref#semantic",
    "source_sha256": "0839c67cebc17d313e5941d64c36f79d4465b30a670f79b7d84f0344998a3582",
    "fingerprint": "639b7736ec264101534ee98ae545005940156236a991d942cbd590cb370faf11",
}
FROZEN_WORK_LINEAGE_A = {
    "schema_version": 1,
    "fingerprint": FROZEN_WORK_SHA,
}

FROZEN_PROJECTION_B = {
    "stage_id": "P1-00b",
    "scenario_key": "ae00_provider_identity_tests",
    "gap_reference": "docs/ref#semantic",
    "source_sha256": "c3121fb027ccdf450c06ee58378b691d790e7210efb6d8b6c7cb26c3b887d22f",
    "fingerprint": "438c0424c9f9604b269d6be92419ac6e572a0a1bd27088901780231e510c96cf",
}
FROZEN_WORK_LINEAGE_B = {
    "schema_version": 1,
    "fingerprint": FROZEN_WORK_SHA,
}

FROZEN_AST_HASH = "6a1ec137f80e58182be2a136fb17bfc58235931d033a48d4bf4fc61636ccf977"
SENTINEL_SECRET = "SOURCE_FREE_SENTINEL_SECRET_TOKEN_XYZ999"


def _write_turn_stream(path: Path) -> None:
    event = {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 80,
            "output_tokens": 10,
            "reasoning_output_tokens": 2,
        },
    }
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")


def _setup_fixture(
    root: Path,
    state_root: Path,
    run_dir: Path,
    worker_id: str,
    reviewer_id: str | None,
    task_id: str,
    head: str,
    manifest_sha: str,
    controller_run_id: str | None,
    blocked: bool = False,
) -> dict:
    proposals_dir = root / ".local/two-site-refactor/proposals"
    worker_dir = proposals_dir / worker_id
    worker_dir.mkdir(parents=True, exist_ok=True)

    sym_groups = [{"path": "scripts/two_site_provider_metrics.py", "symbols": ["provider_metrics"]}]
    manifest = {"scripts/two_site_provider_metrics.py": manifest_sha}
    ident = build_identity(
        "P1-00b", "docs/ref#semantic", "ae00_provider_identity_tests", sym_groups, manifest,
    )
    payload = {"gap_reference": "docs/ref#semantic", "semantic_identity": ident}
    goal = f"Preamble sentinel: {SENTINEL_SECRET}\nCANONICAL_SOURCE_EXCERPTS_JSON:\n" + json.dumps(payload, sort_keys=True)
    task_data = {
        "task_id": task_id,
        "goal": goal,
        "requirements": ["R1"],
        "test_selectors": ["S1"],
    }
    task_snapshot = {"source_head": head, "task": task_data}
    (worker_dir / "task.json").write_text(json.dumps(task_snapshot), encoding="utf-8")

    worker_status = {
        "run_id": worker_id,
        "task_id": task_id,
        "head": head,
        "role": "worker",
        "state": "BLOCKED" if blocked else "PROPOSAL_READY",
        "duration_seconds": 5.0,
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    }
    if controller_run_id is not None:
        worker_status["controller_run_id"] = controller_run_id
    (worker_dir / "status.json").write_text(json.dumps(worker_status), encoding="utf-8")

    if reviewer_id is not None:
        reviewer_dir = proposals_dir / reviewer_id
        reviewer_dir.mkdir(parents=True, exist_ok=True)
        (reviewer_dir / "task.json").write_text(json.dumps(task_snapshot), encoding="utf-8")
        reviewer_status = {
            "run_id": reviewer_id,
            "task_id": task_id,
            "head": head,
            "role": "verifier",
            "state": "VERIFIED",
            "duration_seconds": 5.0,
            "input_worker_run_id": worker_id,
            "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        }
        if controller_run_id is not None:
            reviewer_status["controller_run_id"] = controller_run_id
        (reviewer_dir / "status.json").write_text(json.dumps(reviewer_status), encoding="utf-8")

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.txt").write_text(f"Prompt sentinel: {SENTINEL_SECRET}", encoding="utf-8")
    _write_turn_stream(run_dir / "stream.jsonl")

    meta = {
        "state": "BLOCKED" if blocked else "CONTINUE",
        "head_before": head,
        "head_after": head if blocked else "b" * 40,
        "gemini_worker_run_id": worker_id,
        "duration_seconds": 5.0 if blocked else 10.0,
    }
    if reviewer_id is not None:
        meta["gemini_reviewer_run_id"] = reviewer_id
        meta["proposal_sha256"] = "f" * 64
    return meta


def _assert_cycle_witness(
    record: dict, ledger: list[dict], expected_work: dict, expected_semantic: dict,
) -> None:
    assert record.get("work_lineage") == expected_work
    assert record.get("semantic_identity") == expected_semantic
    assert ledger[-1].get("work_lineage") == expected_work
    assert ledger[-1].get("semantic_identity") == expected_semantic


def test_actual_cycle_work_projection_and_fallback(tmp_path: Path) -> None:
    raw_work = json.dumps(
        FROZEN_WORK_PAYLOAD,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    assert hashlib.sha256(raw_work).hexdigest() == FROZEN_WORK_SHA

    state_root = tmp_path / ".local/two-site-refactor/autopilot"
    state_root.mkdir(parents=True, exist_ok=True)
    head = "a" * 40

    cases = [
        (
            "ca-worker-0",
            "ca-reviewer-0",
            "task-ca",
            "1" * 64,
            "run-ca",
            False,
            FROZEN_PROJECTION_A,
            FROZEN_WORK_LINEAGE_A,
            {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
            2,
            True,
        ),
        (
            "cb-worker-0",
            "cb-reviewer-0",
            "task-cb",
            "2" * 64,
            "run-cb",
            False,
            FROZEN_PROJECTION_B,
            FROZEN_WORK_LINEAGE_B,
            {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
            2,
            True,
        ),
        (
            "ba-worker-0",
            None,
            "task-ba",
            "1" * 64,
            "run-ba",
            True,
            FROZEN_PROJECTION_A,
            FROZEN_WORK_LINEAGE_A,
            {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            1,
            None,
        ),
        (
            "leg-worker-0",
            "leg-reviewer-0",
            "task-leg",
            "1" * 64,
            None,
            False,
            FROZEN_PROJECTION_A,
            FROZEN_WORK_LINEAGE_A,
            {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
            2,
            True,
        ),
    ]

    for idx, (
        w_id, r_id, t_id, m_sha, ctrl_run, blocked, exp_sem, exp_work, exp_usage, exp_att, exp_first_pass
    ) in enumerate(cases):
        run_name = f"cycle-{idx:04d}"
        run_dir = state_root / "runs" / run_name
        effective_ctrl = run_name if ctrl_run is not None else None
        meta = _setup_fixture(
            tmp_path, state_root, run_dir, w_id, r_id, t_id, head, m_sha, effective_ctrl, blocked=blocked,
        )

        proposals_dir = tmp_path / ".local/two-site-refactor/proposals"
        file_snaps = {}
        for p in proposals_dir.rglob("*.json"):
            file_snaps[str(p)] = p.read_bytes()
        meta_snap = copy.deepcopy(meta)

        record = metrics.record_cycle(tmp_path, state_root, run_dir, meta, "astra", "xhigh")

        assert meta == meta_snap
        for p_str, expected_bytes in file_snaps.items():
            assert Path(p_str).read_bytes() == expected_bytes

        assert record["work_lineage"] == exp_work
        assert record["work_lineage"] is not exp_work
        assert record["semantic_identity"] == exp_sem
        assert record["semantic_identity"] is not exp_sem

        prov = record.get("provider_metrics", {})
        assert prov.get("usage") == exp_usage
        assert prov.get("observed_usage_subtotal") == exp_usage
        assert prov.get("attempts") == exp_att
        assert prov.get("usage_observed_attempts") == exp_att
        assert prov.get("first_pass") == exp_first_pass

        assert record["input_tokens"] == 100
        assert record["cached_input_tokens"] == 80
        assert record["uncached_input_tokens"] == 20
        assert record["output_tokens"] == 10
        assert record["reasoning_output_tokens"] == 2

        per_run_raw = bounded_file_text(tmp_path, run_dir / "metrics.json", 8000, "run metrics")
        assert SENTINEL_SECRET not in per_run_raw
        assert isinstance(unique_json(per_run_raw), dict)

        ledger_raw = bounded_file_text(tmp_path, state_root / "metrics.json", metrics.MAX_LEDGER, "ledger")
        assert SENTINEL_SECRET not in ledger_raw
        ledger_data = unique_json(ledger_raw)
        assert isinstance(ledger_data, list)
        assert ledger_data[-1]["work_lineage"] == exp_work
        assert ledger_data[-1]["semantic_identity"] == exp_sem


def test_cycle_projection_validation_legacy_and_link_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    valid_in = {"schema_version": 1, "fingerprint": "a" * 64}
    saved_in = copy.deepcopy(valid_in)
    res = metrics.work_lineage(valid_in)
    assert res == saved_in
    assert res is not valid_in
    valid_in["schema_version"] = 2
    valid_in["fingerprint"] = "b" * 64
    assert res == saved_in

    invalid_cases = [
        None,
        123,
        "string",
        [],
        {},
        {"schema_version": 1},
        {"fingerprint": "a" * 64},
        {"schema_version": 1, "fingerprint": "a" * 64, "extra": True},
        {"schema_version": 0, "fingerprint": "a" * 64},
        {"schema_version": 2, "fingerprint": "a" * 64},
        {"schema_version": True, "fingerprint": "a" * 64},
        {"schema_version": 1, "fingerprint": "A" * 64},
        {"schema_version": 1, "fingerprint": "a" * 63},
        {"schema_version": 1, "fingerprint": "a" * 65},
        {"schema_version": 1, "fingerprint": "g" * 64},
        {"schema_version": 1, "fingerprint": 12345},
    ]
    for bad in invalid_cases:
        assert metrics.work_lineage(bad) is None

    state_root = tmp_path / ".local/two-site-refactor/autopilot"
    state_root.mkdir(parents=True, exist_ok=True)
    run_dir = state_root / "runs" / "guard-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.txt").write_text("guard prompt", encoding="utf-8")
    _write_turn_stream(run_dir / "stream.jsonl")

    monkeypatch.setattr(
        metrics, "_call_provider_metrics",
        lambda *a, **kw: {"state": "OK", "semantic_identity": copy.deepcopy(FROZEN_PROJECTION_A)},
    )
    rec_ok = metrics.record_cycle(tmp_path, state_root, run_dir, {"state": "CONTINUE"}, "astra", "xhigh")
    assert rec_ok["semantic_identity"] == FROZEN_PROJECTION_A
    assert "work_lineage" not in rec_ok

    monkeypatch.setattr(
        metrics, "_call_provider_metrics",
        lambda *a, **kw: {
            "state": "PARTIAL",
            "semantic_identity": copy.deepcopy(FROZEN_PROJECTION_A),
            "work_lineage": copy.deepcopy(FROZEN_WORK_LINEAGE_A),
        },
    )
    rec_partial = metrics.record_cycle(tmp_path, state_root, run_dir, {"state": "CONTINUE"}, "astra", "xhigh")
    assert rec_partial["semantic_identity"] == FROZEN_PROJECTION_A
    assert "work_lineage" not in rec_partial

    monkeypatch.setattr(
        metrics, "_call_provider_metrics",
        lambda *a, **kw: {
            "state": "OBSERVED_CONTROLLER_ATTEMPTS",
            "semantic_identity": None,
            "work_lineage": copy.deepcopy(FROZEN_WORK_LINEAGE_A),
        },
    )
    rec_no_ident = metrics.record_cycle(tmp_path, state_root, run_dir, {"state": "CONTINUE"}, "astra", "xhigh")
    assert "semantic_identity" not in rec_no_ident
    assert "work_lineage" not in rec_no_ident

    proposals_dir = tmp_path / ".local/two-site-refactor/proposals"
    drift_worker_dir = proposals_dir / "drift-worker"
    drift_worker_dir.mkdir(parents=True, exist_ok=True)
    (drift_worker_dir / "task.json").write_text(
        json.dumps({"source_head": "0" * 40, "task": {"task_id": "task-drift", "goal": "drift goal"}}),
        encoding="utf-8",
    )
    run_drift = state_root / "runs" / "fallback-drift-run"
    run_drift.mkdir(parents=True, exist_ok=True)
    (run_drift / "prompt.txt").write_text("prompt", encoding="utf-8")
    _write_turn_stream(run_drift / "stream.jsonl")

    monkeypatch.setattr(
        metrics, "_call_provider_metrics",
        lambda *a, **kw: {"state": "UNAVAILABLE", "semantic_identity": copy.deepcopy(FROZEN_PROJECTION_A)},
    )
    meta_drift = {
        "state": "CONTINUE",
        "head_before": "a" * 40,
        "gemini_worker_run_id": "drift-worker",
    }
    rec_drift = metrics.record_cycle(tmp_path, state_root, run_drift, meta_drift, "astra", "xhigh")
    assert "semantic_identity" not in rec_drift
    assert "work_lineage" not in rec_drift
    assert rec_drift.get("task_metadata_state") == "UNAVAILABLE"

    err_worker_dir = proposals_dir / "err-worker"
    err_worker_dir.mkdir(parents=True, exist_ok=True)
    (err_worker_dir / "task.json").write_text(
        json.dumps({"source_head": "a" * 40, "task": {"task_id": "task-err", "goal": "err goal"}}),
        encoding="utf-8",
    )
    run_err = state_root / "runs" / "fallback-err-run"
    run_err.mkdir(parents=True, exist_ok=True)
    (run_err / "prompt.txt").write_text("prompt", encoding="utf-8")
    _write_turn_stream(run_err / "stream.jsonl")

    def _fallback_crash_provider(root, task_id=None, *args, **kwargs):
        if task_id is None:
            return {"state": "UNAVAILABLE", "semantic_identity": copy.deepcopy(FROZEN_PROJECTION_A)}
        raise OSError("fallback provider error")

    monkeypatch.setattr(metrics, "_call_provider_metrics", _fallback_crash_provider)
    meta_err = {
        "state": "CONTINUE",
        "head_before": "a" * 40,
        "gemini_worker_run_id": "err-worker",
    }
    rec_err = metrics.record_cycle(tmp_path, state_root, run_err, meta_err, "astra", "xhigh")
    assert "semantic_identity" not in rec_err
    assert "work_lineage" not in rec_err
    assert rec_err["provider_metrics"]["state"] == "UNAVAILABLE"
    assert rec_err["provider_metrics"]["diagnostic"] == "OSError"

    isolated_state = tmp_path / "link_guard" / ".local/two-site-refactor/autopilot"
    isolated_run = isolated_state / "runs" / "link_run"
    isolated_run.mkdir(parents=True, exist_ok=True)
    per_run_metrics_path = isolated_run / "metrics.json"
    ledger_metrics_path = isolated_state / "metrics.json"

    external = tmp_path / "distinct_external.json"
    external_bytes = b'{"external": "target"}\n'
    external.write_bytes(external_bytes)
    ext_stat = external.stat()
    ext_ino = ext_stat.st_ino

    per_run_metrics_path.symlink_to(external)
    with pytest.raises(ProposalError):
        bounded_file_text(tmp_path, per_run_metrics_path, 8000, "run metrics symlink")
    assert os.path.islink(per_run_metrics_path)
    assert external.read_bytes() == external_bytes
    assert external.stat().st_ino == ext_ino
    assert external.stat().st_nlink == 1
    per_run_metrics_path.unlink()

    os.link(external, per_run_metrics_path)
    with pytest.raises(ProposalError):
        bounded_file_text(tmp_path, per_run_metrics_path, 8000, "run metrics hardlink")
    assert per_run_metrics_path.is_file()
    assert external.read_bytes() == external_bytes
    assert external.stat().st_ino == ext_ino
    assert external.stat().st_nlink == 2
    per_run_metrics_path.unlink()
    assert external.stat().st_nlink == 1

    ledger_metrics_path.symlink_to(external)
    with pytest.raises(ProposalError):
        bounded_file_text(tmp_path, ledger_metrics_path, metrics.MAX_LEDGER, "ledger metrics symlink")
    assert os.path.islink(ledger_metrics_path)
    assert external.read_bytes() == external_bytes
    assert external.stat().st_ino == ext_ino
    assert external.stat().st_nlink == 1
    ledger_metrics_path.unlink()

    os.link(external, ledger_metrics_path)
    with pytest.raises(ProposalError):
        bounded_file_text(tmp_path, ledger_metrics_path, metrics.MAX_LEDGER, "ledger metrics hardlink")
    assert ledger_metrics_path.is_file()
    assert external.read_bytes() == external_bytes
    assert external.stat().st_ino == ext_ino
    assert external.stat().st_nlink == 2
    ledger_metrics_path.unlink()
    assert external.stat().st_nlink == 1

    tree = ast.parse(Path(metrics.__file__).read_text(encoding="utf-8"))
    fn_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "summarize_efficiency_ledger"
    )
    dump_bytes = ast.dump(fn_node, include_attributes=False).encode("utf-8")
    assert hashlib.sha256(dump_bytes).hexdigest() == FROZEN_AST_HASH


def test_cycle_projection_full_value_witness_mutation_and_bytes(tmp_path: Path) -> None:
    state_root = tmp_path / ".local/two-site-refactor/autopilot"
    state_root.mkdir(parents=True, exist_ok=True)
    head = "a" * 40

    run_dir1 = state_root / "runs" / "wit-0001"
    meta1 = _setup_fixture(
        tmp_path, state_root, run_dir1, "w-wit-1", "r-wit-1", "t-wit-1",
        head, "1" * 64, "wit-0001",
    )
    rec1 = metrics.record_cycle(tmp_path, state_root, run_dir1, meta1, "astra", "xhigh")
    ledger1 = unique_json(bounded_file_text(tmp_path, state_root / "metrics.json", metrics.MAX_LEDGER, "ledger"))
    _assert_cycle_witness(rec1, ledger1, FROZEN_WORK_LINEAGE_A, FROZEN_PROJECTION_A)

    saved_work_lineage = metrics.work_lineage
    try:
        metrics.work_lineage = lambda value: None
        run_dir2 = state_root / "runs" / "wit-0002"
        meta2 = _setup_fixture(
            tmp_path, state_root, run_dir2, "w-wit-2", "r-wit-2", "t-wit-2",
            head, "1" * 64, "wit-0002",
        )
        rec2 = metrics.record_cycle(tmp_path, state_root, run_dir2, meta2, "astra", "xhigh")
        ledger2 = unique_json(bounded_file_text(tmp_path, state_root / "metrics.json", metrics.MAX_LEDGER, "ledger"))
        with pytest.raises(AssertionError):
            _assert_cycle_witness(rec2, ledger2, FROZEN_WORK_LINEAGE_A, FROZEN_PROJECTION_A)
    finally:
        metrics.work_lineage = saved_work_lineage

    run_dir3 = state_root / "runs" / "wit-0003"
    meta3 = _setup_fixture(
        tmp_path, state_root, run_dir3, "w-wit-3", "r-wit-3", "t-wit-3",
        head, "1" * 64, "wit-0003",
    )
    rec3 = metrics.record_cycle(tmp_path, state_root, run_dir3, meta3, "astra", "xhigh")
    ledger3 = unique_json(bounded_file_text(tmp_path, state_root / "metrics.json", metrics.MAX_LEDGER, "ledger"))
    _assert_cycle_witness(rec3, ledger3, FROZEN_WORK_LINEAGE_A, FROZEN_PROJECTION_A)

    test_file_bytes = len(Path(__file__).read_bytes())
    assert test_file_bytes <= 24_000
