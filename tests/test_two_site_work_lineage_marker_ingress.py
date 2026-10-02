"""Hermetic test contracts for two-site work lineage marker ingress."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from scripts import two_site_provider_metrics as metrics
try:
    from scripts.two_site_proposal_runner import MAX_CONTEXT
except ModuleNotFoundError:
    from two_site_proposal_runner import MAX_CONTEXT
from scripts.two_site_semantic_task import build_identity

HEAD = "a" * 40
TOKENS = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14, "thinking_tokens": 3}
CTRL = "ctrl-run-1"
EXPECTED_BASE_SHA = "41de8c6f7d2f61cc10a9a71beb9ef4d33e06b52e6a3dfdffd02266d2a32c10e2"
RAW_MARKER = "CANONICAL_SOURCE_EXCERPTS_JSON:"
CANON_MARKER = "\nCANONICAL_SOURCE_EXCERPTS_JSON:\n"
DOC_REF = "docs/ref#semantic"
PY_PATH = "scripts/two_site_provider_metrics.py"
SCEN_KEY = "ae00_provider_identity_tests"
SYM_GROUPS = [{"path": PY_PATH, "symbols": ["provider_metrics"]}]
EXP_WORK = {"schema_version": 1, "fingerprint": EXPECTED_BASE_SHA}
EXP_USAGE = {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28}


def _make_embedded_marker_goal(
    gap: str = DOC_REF,
    semantic_identity: dict | None = None,
    extra_preamble: str = "",
    sentinel: str | None = None,
) -> str:
    text = f'marker = "{RAW_MARKER}"\nescaped = "\\\n{RAW_MARKER}\\\n"\n'
    if sentinel is not None:
        text += f'sentinel = "{sentinel}"\n'
    data: dict[str, object] = {
        "gap_reference": gap,
        "excerpts": [{"path": PY_PATH, "ranges": [{"text": text}]}],
    }
    if semantic_identity is not None:
        data["semantic_identity"] = semantic_identity
    return f"Goal description{extra_preamble}{CANON_MARKER}{json.dumps(data)}"


def _make_attempt(
    root: Path,
    name: str,
    controller_run_id: str | None = CTRL,
    task_id: str = "task",
    head: str = HEAD,
    role: str = "worker",
    state: str = "PROPOSAL_READY",
    usage: dict | None = TOKENS,
    duration_seconds: float | None = 5.0,
    gap: str = DOC_REF,
    semantic_identity: dict | None = None,
    raw_goal: str | None = None,
    sentinel: str | None = None,
    **extra_status,
) -> Path:
    path = root / ".local/two-site-refactor/proposals" / name
    path.mkdir(parents=True, exist_ok=True)
    status = {
        "run_id": name, "task_id": task_id, "head": head, "role": role,
        "state": state, "usage": usage, "duration_seconds": duration_seconds,
    }
    if controller_run_id is not None:
        status["controller_run_id"] = controller_run_id
    status.update(extra_status)
    (path / "status.json").write_text(json.dumps(status))

    goal = raw_goal if raw_goal is not None else _make_embedded_marker_goal(
        gap=gap, semantic_identity=semantic_identity, sentinel=sentinel
    )
    task_payload = {
        "source_head": head,
        "task": {
            "task_id": task_id, "goal": goal,
            "requirements": ["r1"], "test_selectors": ["tests/test_mod.py"],
        },
    }
    (path / "task.json").write_text(json.dumps(task_payload))
    return path


def _make_pair(
    root: Path,
    prefix: str = "crun",
    controller_run_id: str | None = CTRL,
    task_id: str = "task",
    head: str = HEAD,
    gap: str = DOC_REF,
    semantic_identity: dict | None = None,
    raw_goal: str | None = None,
    worker_state: str = "PROPOSAL_READY",
    reviewer_state: str = "VERIFIED",
    worker_usage: dict | None = TOKENS,
    reviewer_usage: dict | None = TOKENS,
    sentinel: str | None = None,
) -> tuple[Path, Path]:
    w = _make_attempt(
        root, f"{prefix}-worker-0", controller_run_id=controller_run_id, task_id=task_id,
        head=head, role="worker", state=worker_state, usage=worker_usage, gap=gap,
        semantic_identity=semantic_identity, raw_goal=raw_goal, sentinel=sentinel,
    )
    r = _make_attempt(
        root, f"{prefix}-reviewer-0", controller_run_id=controller_run_id, task_id=task_id,
        head=head, role="verifier", state=reviewer_state, usage=reviewer_usage,
        input_worker_run_id=f"{prefix}-worker-0", gap=gap,
        semantic_identity=semantic_identity, raw_goal=raw_goal, sentinel=sentinel,
    )
    return w, r


def _pm(root: Path, **overrides) -> dict:
    args = {"task_id": "task", "head_before": HEAD, "controller_run_id": CTRL, "cycle_duration": 50.0}
    args.update(overrides)
    return metrics.provider_metrics(root, **args)


def _assert_rejected(res: dict) -> None:
    assert "work_lineage" not in res and res["semantic_identity"] is None


def test_actual_collector_accepts_embedded_marker_with_one_canonical_delimiter(tmp_path):
    ident = build_identity(
        stage_id="P1-00b", gap_reference=DOC_REF, scenario_key=SCEN_KEY,
        symbol_groups=SYM_GROUPS, source_manifest={PY_PATH: "a" * 64},
    )
    expected_sem = {
        "fingerprint": ident["fingerprint"], "source_sha256": ident["source_sha256"],
        "stage_id": ident["stage_id"], "gap_reference": ident["gap_reference"],
        "scenario_key": ident["scenario_key"],
    }
    expected_meta = {
        "task_id": "task", "declared_requirement_count": 1,
        "focused_selector_count": 1, "gap_reference": DOC_REF,
    }

    goal_text = _make_embedded_marker_goal(semantic_identity=ident)
    assert goal_text.count(CANON_MARKER) == 1
    assert goal_text.count(RAW_MARKER) >= 2

    root_legacy = tmp_path / "valid_legacy"
    _make_pair(root_legacy, controller_run_id=None, semantic_identity=ident)
    res_legacy = metrics.provider_metrics(
        root_legacy, task_id="task", head_before=HEAD, final_worker_id="crun-worker-0",
        cycle_duration=50.0, controller_run_id=None,
    )
    assert res_legacy["state"] == "OBSERVED_MATCHING_ATTEMPTS"
    assert res_legacy["work_lineage"] == EXP_WORK
    assert res_legacy["semantic_identity"] == expected_sem
    assert res_legacy["task_metadata"] is not None
    for k, v in expected_meta.items():
        assert res_legacy["task_metadata"][k] == v
    assert res_legacy["first_pass"] is True
    assert res_legacy["attempts"] == 2
    assert res_legacy["failed_attempts"] == 0
    assert res_legacy["usage"] == EXP_USAGE

    root_ctrl = tmp_path / "valid_controller"
    _make_pair(root_ctrl, semantic_identity=ident)
    res_ctrl = _pm(root_ctrl)
    assert res_ctrl["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
    assert res_ctrl["work_lineage"] == EXP_WORK
    assert res_ctrl["semantic_identity"] == expected_sem
    assert res_ctrl["task_metadata"] is not None
    for k, v in expected_meta.items():
        assert res_ctrl["task_metadata"][k] == v
    assert res_ctrl["first_pass"] is True
    assert res_ctrl["attempts"] == 2
    assert res_ctrl["failed_attempts"] == 0
    assert res_ctrl["usage"] == EXP_USAGE

    root_blocked = tmp_path / "blocked_embedded"
    _make_attempt(root_blocked, "crun-worker-0", role="worker", state="BLOCKED", usage=TOKENS, semantic_identity=ident)
    res_b = _pm(root_blocked, cycle_duration=20.0)
    assert res_b["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
    assert res_b["failed_attempts"] == 1
    assert res_b["first_pass"] is None
    assert res_b["usage"] == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    assert res_b["work_lineage"] == EXP_WORK


def test_actual_collector_preserves_rejected_delimiters_and_bindings(tmp_path):
    ident = build_identity(
        stage_id="P1-00b", gap_reference=DOC_REF, scenario_key=SCEN_KEY,
        symbol_groups=SYM_GROUPS, source_manifest={PY_PATH: "a" * 64},
    )
    expected_sem = {
        "fingerprint": ident["fingerprint"], "source_sha256": ident["source_sha256"],
        "stage_id": ident["stage_id"], "gap_reference": ident["gap_reference"],
        "scenario_key": ident["scenario_key"],
    }
    base_goal = _make_embedded_marker_goal(semantic_identity=ident)
    payload_dict = {"gap_reference": DOC_REF, "semantic_identity": ident}

    _make_pair(tmp_path / "bad_dup", raw_goal=f'{base_goal}{CANON_MARKER}{{"gap_reference":"{DOC_REF}"}}', semantic_identity=ident)
    _assert_rejected(_pm(tmp_path / "bad_dup"))

    _make_pair(tmp_path / "no_delim", raw_goal="Goal with no marker anywhere", semantic_identity=ident)
    _assert_rejected(_pm(tmp_path / "no_delim"))

    inline_root = tmp_path / "bad_inline"
    inline_goal = f"Scope {RAW_MARKER} {json.dumps(payload_dict)}"
    _make_pair(inline_root, raw_goal=inline_goal, semantic_identity=ident)
    _assert_rejected(_pm(inline_root))

    worker_dir = inline_root / ".local/two-site-refactor/proposals/crun-worker-0"
    exp_task_obj = {
        "task_id": "task", "goal": inline_goal,
        "requirements": ["r1"], "test_selectors": ["tests/test_mod.py"],
    }
    exp_task_sha = hashlib.sha256(json.dumps(exp_task_obj, sort_keys=True).encode("utf-8")).hexdigest()
    exp_meta = {
        "task_id": "task", "task_sha256": exp_task_sha,
        "declared_requirement_count": 1, "focused_selector_count": 1, "gap_reference": DOC_REF,
    }
    assert metrics._extract_task_metadata(inline_root, worker_dir, "task", HEAD) == (exp_task_sha, exp_meta)
    assert metrics._extract_identity_bundle(inline_root, worker_dir, "task", HEAD) is None

    _make_pair(tmp_path / "bad_preamble", raw_goal=f"Preamble {RAW_MARKER} extra{CANON_MARKER}{json.dumps(payload_dict)}", semantic_identity=ident)
    _assert_rejected(_pm(tmp_path / "bad_preamble"))

    _make_pair(tmp_path / "bad_malformed", raw_goal=f"Preamble{CANON_MARKER}{json.dumps(payload_dict)} trailing garbage", semantic_identity=ident)
    _assert_rejected(_pm(tmp_path / "bad_malformed"))

    _make_pair(tmp_path / "bad_dup_key", raw_goal=f'Preamble{CANON_MARKER}{{"gap_reference":"{DOC_REF}","gap_reference":"{DOC_REF}"}}', semantic_identity=ident)
    _assert_rejected(_pm(tmp_path / "bad_dup_key"))

    _make_pair(tmp_path / "bad_incomplete", semantic_identity={k: v for k, v in ident.items() if k != "symbol_groups"})
    _assert_rejected(_pm(tmp_path / "bad_incomplete"))

    tm_root = tmp_path / "bad_task"
    _make_attempt(tm_root, "crun-worker-0", task_id="task", semantic_identity=ident)
    _make_attempt(tm_root, "crun-reviewer-0", role="verifier", state="VERIFIED", input_worker_run_id="crun-worker-0", task_id="diff-task", semantic_identity=ident)
    res_tm = _pm(tm_root)
    assert res_tm["state"] == "PARTIAL" and "work_lineage" not in res_tm

    hm_root = tmp_path / "bad_head"
    _make_attempt(hm_root, "crun-worker-0", head=HEAD, semantic_identity=ident)
    _make_attempt(hm_root, "crun-reviewer-0", role="verifier", state="VERIFIED", input_worker_run_id="crun-worker-0", head="b" * 40, semantic_identity=ident)
    res_hm = _pm(hm_root)
    assert res_hm["state"] == "PARTIAL" and "work_lineage" not in res_hm

    _make_pair(tmp_path / "bad_gap", raw_goal=f"Preamble{CANON_MARKER}{json.dumps({'gap_reference': 'docs/ref#different', 'semantic_identity': ident})}", semantic_identity=ident)
    _assert_rejected(_pm(tmp_path / "bad_gap"))

    _make_pair(tmp_path / "bad_fp", semantic_identity=dict(ident, fingerprint="0" * 64))
    _assert_rejected(_pm(tmp_path / "bad_fp"))

    _make_pair(tmp_path / "bad_mf", semantic_identity=dict(ident, source_manifest={PY_PATH: "0" * 64}))
    _assert_rejected(_pm(tmp_path / "bad_mf"))

    wm_root = tmp_path / "diff_id"
    id_rev = build_identity(
        stage_id="P1-00b", gap_reference=DOC_REF, scenario_key="ae00_other_key",
        symbol_groups=SYM_GROUPS, source_manifest={PY_PATH: "a" * 64},
    )
    _make_attempt(wm_root, "crun-worker-0", semantic_identity=ident)
    _make_attempt(wm_root, "crun-reviewer-0", role="verifier", state="VERIFIED", input_worker_run_id="crun-worker-0", semantic_identity=id_rev)
    res_wm = _pm(wm_root)
    assert res_wm["state"] == "PARTIAL" and "work_lineage" not in res_wm

    rest_root = tmp_path / "restored_base"
    _make_pair(rest_root, semantic_identity=ident)
    res_rest = _pm(rest_root)
    assert res_rest["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
    assert res_rest["work_lineage"] == EXP_WORK
    assert res_rest["semantic_identity"] == expected_sem

    id_a = build_identity("P1-00b", DOC_REF, SCEN_KEY, SYM_GROUPS, {PY_PATH: "1" * 64})
    id_b = build_identity("P1-00b", DOC_REF, SCEN_KEY, SYM_GROUPS, {PY_PATH: "2" * 64})
    root_a, root_b = tmp_path / "src_a", tmp_path / "src_b"
    _make_pair(root_a, semantic_identity=id_a)
    _make_pair(root_b, semantic_identity=id_b)
    res_a, res_b = _pm(root_a), _pm(root_b)
    assert res_a["work_lineage"] == res_b["work_lineage"] == EXP_WORK
    assert res_a["semantic_identity"]["source_sha256"] != res_b["semantic_identity"]["source_sha256"]
    assert res_a["semantic_identity"]["fingerprint"] != res_b["semantic_identity"]["fingerprint"]


def test_guard_mutation_same_collector_witness_and_source_free_usage(tmp_path):
    assert MAX_CONTEXT == 160_000
    assert metrics.TASK_SNAPSHOT_LIMIT == 480_000
    assert metrics.TASK_GOAL_LIMIT == 160_000

    def _assert_complete(val: dict) -> None:
        assert val["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
        assert val["work_lineage"] == EXP_WORK
        assert set(val["work_lineage"].keys()) == {"schema_version", "fingerprint"}
        assert val["usage"] == EXP_USAGE

    root = tmp_path / "collector_witness"
    sentinel = "SECRET_SENTINEL_TOKEN_EXCERPT_NO_LEAK_12345"
    ident = build_identity(
        stage_id="P1-00b", gap_reference=DOC_REF, scenario_key=SCEN_KEY,
        symbol_groups=SYM_GROUPS, source_manifest={PY_PATH: "a" * 64},
    )
    _make_attempt(root, "crun-worker-0", semantic_identity=ident, prompt=sentinel, sentinel=sentinel)
    _make_attempt(root, "crun-reviewer-0", role="verifier", state="VERIFIED", input_worker_run_id="crun-worker-0", semantic_identity=ident, prompt=sentinel, sentinel=sentinel)

    p_w = root / ".local/two-site-refactor/proposals/crun-worker-0"
    p_r = root / ".local/two-site-refactor/proposals/crun-reviewer-0"
    worker_task, worker_status = p_w / "task.json", p_w / "status.json"
    rev_task, rev_status = p_r / "task.json", p_r / "status.json"

    worker_task_bytes = worker_task.read_bytes()
    worker_status_bytes = worker_status.read_bytes()
    rev_task_bytes = rev_task.read_bytes()
    rev_status_bytes = rev_status.read_bytes()

    assert sentinel.encode() in worker_task_bytes
    assert sentinel.encode() in worker_status_bytes

    res_initial = _pm(root)
    _assert_complete(res_initial)
    assert sentinel not in json.dumps(res_initial)

    assert worker_task.read_bytes() == worker_task_bytes
    assert worker_status.read_bytes() == worker_status_bytes
    assert rev_task.read_bytes() == rev_task_bytes
    assert rev_status.read_bytes() == rev_status_bytes

    orig_helper = metrics._extract_canonical_payload

    def old_rejection_guard(goal: str) -> dict | None:
        if not isinstance(goal, str) or goal.count(RAW_MARKER) != 1:
            return None
        return orig_helper(goal)

    with mock.patch.object(metrics, "_extract_canonical_payload", side_effect=old_rejection_guard):
        with pytest.raises(AssertionError):
            _assert_complete(_pm(root))
        res_mutated = _pm(root)
        assert res_mutated["state"] == "PARTIAL"
        assert "work_lineage" not in res_mutated
        assert res_mutated["semantic_identity"] is None
        assert res_mutated["task_metadata"] is None

    _assert_complete(_pm(root))
    assert sentinel not in json.dumps(_pm(root))

    outside = tmp_path / "outside_dir"
    outside.mkdir(parents=True, exist_ok=True)
    outside_file = outside / "ext_task.json"
    outside_file.write_bytes(worker_task_bytes)
    worker_task.unlink()
    worker_task.symlink_to(outside_file)
    res_sym = _pm(root)
    assert res_sym["state"] == "PARTIAL" and "work_lineage" not in res_sym
    worker_task.unlink()
    worker_task.write_bytes(worker_task_bytes)
    _assert_complete(_pm(root))

    hardlink_target = outside / "hl_target.json"
    os.link(worker_task, hardlink_target)
    res_hl = _pm(root)
    assert res_hl["state"] == "PARTIAL" and "work_lineage" not in res_hl
    hardlink_target.unlink()
    worker_task.unlink()
    worker_task.write_bytes(worker_task_bytes)
    _assert_complete(_pm(root))

    pad_len = 480_000 - len(worker_task_bytes)
    assert pad_len > 0
    padded_480000 = worker_task_bytes + b" " * pad_len
    assert len(padded_480000) == 480_000
    worker_task.write_bytes(padded_480000)
    _assert_complete(_pm(root))

    worker_task.write_bytes(padded_480000 + b" ")
    assert len(worker_task.read_bytes()) == 480_001
    res_over = _pm(root)
    assert res_over["state"] == "PARTIAL"
    assert res_over["task_metadata"] is None
    assert "work_lineage" not in res_over
    assert res_over["semantic_identity"] is None

    worker_task.write_bytes(worker_task_bytes)
    _assert_complete(_pm(root))

    root_inv = tmp_path / "all_invalid"
    _make_attempt(root_inv, "crun-worker-0", role="worker", state="INVALID_STATE", usage=TOKENS, semantic_identity=ident)
    res_inv = _pm(root_inv)
    assert res_inv["state"] == "PARTIAL"
    assert res_inv["usage"] == {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    assert res_inv["observed_usage_subtotal"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert res_inv["usage_observed_attempts"] == 0
    assert res_inv["rework_taxonomy"]["initial_outcome"] == "UNKNOWN"
    assert res_inv["rework_taxonomy"]["terminal_outcome"] == "UNKNOWN"


def test_candidate_utf8_cap_and_legacy_schema():
    raw_bytes = Path(__file__).read_bytes()
    assert 0 < len(raw_bytes) <= 24000

    unav = metrics.unavailable()
    assert set(unav.keys()) == {
        "state", "first_pass", "usage", "phase_seconds",
        "task_metadata", "semantic_identity", "session_gate", "rework_taxonomy",
    }
    assert "work_lineage" not in unav
    assert unav["state"] == "UNAVAILABLE"
    assert unav["first_pass"] is None
    assert unav["task_metadata"] is None
    assert unav["semantic_identity"] is None
    assert unav["usage"] == {"input_tokens": None, "output_tokens": None, "total_tokens": None}
