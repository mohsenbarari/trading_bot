"""Hermetic test contracts for two-site work identity producer."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts import two_site_provider_metrics as metrics
from scripts.two_site_semantic_task import build_identity

HEAD = "a" * 40
TOKENS = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14, "thinking_tokens": 3}
CTRL = "ctrl-run-1"

BASE_CANONICAL_JSON = (
    '{"gap_reference":"docs/ref#semantic","kind":"two-site-logical-work",'
    '"scenario_key":"ae00_provider_identity_tests","schema_version":1,'
    '"stage_id":"P1-00b","symbol_groups":[{"path":"scripts/two_site_provider_metrics.py","symbols":["provider_metrics"]}]}'
)
EXPECTED_BASE_SHA = "41de8c6f7d2f61cc10a9a71beb9ef4d33e06b52e6a3dfdffd02266d2a32c10e2"


def _make_identity(
    stage_id: str = "P1-00b",
    gap_reference: str = "docs/ref#semantic",
    scenario_key: str = "ae00_provider_identity_tests",
    symbol_groups: list[dict] | None = None,
    source_manifest: dict[str, str | None] | None = None,
) -> dict:
    if symbol_groups is None:
        symbol_groups = [{"path": "scripts/two_site_provider_metrics.py", "symbols": ["provider_metrics"]}]
    if source_manifest is None:
        source_manifest = {"scripts/two_site_provider_metrics.py": "a" * 64}
    return build_identity(stage_id, gap_reference, scenario_key, symbol_groups, source_manifest)


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
    gap: str = "docs/ref#semantic",
    semantic_identity: dict | None = None,
    raw_goal: str | None = None,
    with_task: bool = True,
    **extra_status,
) -> Path:
    path = root / ".local/two-site-refactor/proposals" / name
    path.mkdir(parents=True, exist_ok=True)
    status = {
        "run_id": name,
        "task_id": task_id,
        "head": head,
        "role": role,
        "state": state,
        "usage": usage,
        "duration_seconds": duration_seconds,
    }
    if controller_run_id is not None:
        status["controller_run_id"] = controller_run_id
    status.update(extra_status)
    (path / "status.json").write_text(json.dumps(status))

    if with_task:
        if raw_goal is not None:
            goal = raw_goal
        else:
            excerpt_data: dict[str, object] = {"gap_reference": gap}
            if semantic_identity is not None:
                excerpt_data["semantic_identity"] = semantic_identity
            goal = f"Scope description\nCANONICAL_SOURCE_EXCERPTS_JSON:\n{json.dumps(excerpt_data)}"
        task_payload = {
            "source_head": head,
            "task": {
                "task_id": task_id,
                "goal": goal,
                "requirements": ["r1"],
                "test_selectors": ["tests/test_mod.py"],
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
    gap: str = "docs/ref#semantic",
    semantic_identity: dict | None = None,
    raw_goal: str | None = None,
) -> tuple[Path, Path]:
    worker = _make_attempt(
        root, f"{prefix}-worker-0", controller_run_id=controller_run_id, task_id=task_id,
        head=head, role="worker", state="PROPOSAL_READY", gap=gap,
        semantic_identity=semantic_identity, raw_goal=raw_goal,
    )
    reviewer = _make_attempt(
        root, f"{prefix}-reviewer-0", controller_run_id=controller_run_id, task_id=task_id,
        head=head, role="verifier", state="VERIFIED", input_worker_run_id=f"{prefix}-worker-0",
        gap=gap, semantic_identity=semantic_identity, raw_goal=raw_goal,
    )
    return worker, reviewer


def _pm(root: Path, **overrides) -> dict:
    args = {"task_id": "task", "head_before": HEAD, "controller_run_id": CTRL, "cycle_duration": 50.0}
    args.update(overrides)
    return metrics.provider_metrics(root, **args)


def test_new_candidate_file_respects_utf8_byte_cap():
    raw_bytes = Path(__file__).read_bytes()
    assert 0 < len(raw_bytes) <= 24000


def test_actual_work_hash_changes_only_logical_dimensions(tmp_path):
    assert hashlib.sha256(BASE_CANONICAL_JSON.encode("utf-8")).hexdigest() == EXPECTED_BASE_SHA

    base_root = tmp_path / "base"
    _make_pair(base_root, semantic_identity=_make_identity())
    res_base = _pm(base_root)
    assert res_base["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
    assert res_base["work_lineage"] == {"schema_version": 1, "fingerprint": EXPECTED_BASE_SHA}

    cases = [
        (
            "stage", {"stage_id": "P1-00c"},
            '{"gap_reference":"docs/ref#semantic","kind":"two-site-logical-work",'
            '"scenario_key":"ae00_provider_identity_tests","schema_version":1,'
            '"stage_id":"P1-00c","symbol_groups":[{"path":"scripts/two_site_provider_metrics.py","symbols":["provider_metrics"]}]}',
        ),
        (
            "gap", {"gap_reference": "docs/ref#other"},
            '{"gap_reference":"docs/ref#other","kind":"two-site-logical-work",'
            '"scenario_key":"ae00_provider_identity_tests","schema_version":1,'
            '"stage_id":"P1-00b","symbol_groups":[{"path":"scripts/two_site_provider_metrics.py","symbols":["provider_metrics"]}]}',
        ),
        (
            "scenario", {"scenario_key": "ae00_other_key"},
            '{"gap_reference":"docs/ref#semantic","kind":"two-site-logical-work",'
            '"scenario_key":"ae00_other_key","schema_version":1,'
            '"stage_id":"P1-00b","symbol_groups":[{"path":"scripts/two_site_provider_metrics.py","symbols":["provider_metrics"]}]}',
        ),
        (
            "symbol", {"symbol_groups": [{"path": "scripts/two_site_provider_metrics.py", "symbols": ["other_sym"]}]},
            '{"gap_reference":"docs/ref#semantic","kind":"two-site-logical-work",'
            '"scenario_key":"ae00_provider_identity_tests","schema_version":1,'
            '"stage_id":"P1-00b","symbol_groups":[{"path":"scripts/two_site_provider_metrics.py","symbols":["other_sym"]}]}',
        ),
        (
            "path", {"symbol_groups": [{"path": "scripts/other.py", "symbols": ["provider_metrics"]}]},
            '{"gap_reference":"docs/ref#semantic","kind":"two-site-logical-work",'
            '"scenario_key":"ae00_provider_identity_tests","schema_version":1,'
            '"stage_id":"P1-00b","symbol_groups":[{"path":"scripts/other.py","symbols":["provider_metrics"]}]}',
        ),
    ]

    for label, overrides, expected_json in cases:
        sub_root = tmp_path / f"dim_{label}"
        expected_sha = hashlib.sha256(expected_json.encode("utf-8")).hexdigest()
        assert expected_sha != EXPECTED_BASE_SHA

        ident_kw = {
            "stage_id": "P1-00b", "gap_reference": "docs/ref#semantic",
            "scenario_key": "ae00_provider_identity_tests",
            "symbol_groups": [{"path": "scripts/two_site_provider_metrics.py", "symbols": ["provider_metrics"]}],
            "source_manifest": {"scripts/two_site_provider_metrics.py": "a" * 64, "scripts/other.py": "b" * 64},
        }
        ident_kw.update(overrides)
        ident = _make_identity(**ident_kw)
        _make_pair(sub_root, gap=ident_kw["gap_reference"], semantic_identity=ident)
        res = _pm(sub_root)
        assert res["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
        assert res["work_lineage"] == {"schema_version": 1, "fingerprint": expected_sha}


def test_actual_collector_source_revision_and_task_run_rename_invariance(tmp_path):
    root_a, head_a, task_a, ctrl_a = tmp_path / "rev_a", "1" * 40, "task-alpha", "ctrl-alpha"
    id_a = _make_identity(source_manifest={"scripts/two_site_provider_metrics.py": "1" * 64})
    _make_pair(root_a, prefix="run-alpha", controller_run_id=ctrl_a, task_id=task_a, head=head_a, semantic_identity=id_a)
    res_a = metrics.provider_metrics(root_a, task_id=task_a, head_before=head_a, controller_run_id=ctrl_a, cycle_duration=50.0)

    root_b, head_b, task_b, ctrl_b = tmp_path / "rev_b", "2" * 40, "task-beta", "ctrl-beta"
    id_b = _make_identity(source_manifest={"scripts/two_site_provider_metrics.py": "2" * 64})
    _make_pair(root_b, prefix="run-beta", controller_run_id=ctrl_b, task_id=task_b, head=head_b, semantic_identity=id_b)
    res_b = metrics.provider_metrics(root_b, task_id=task_b, head_before=head_b, controller_run_id=ctrl_b, cycle_duration=50.0)

    assert res_a["state"] == res_b["state"] == "OBSERVED_CONTROLLER_ATTEMPTS"
    assert res_a["work_lineage"] == res_b["work_lineage"] == {"schema_version": 1, "fingerprint": EXPECTED_BASE_SHA}
    assert res_a["semantic_identity"]["fingerprint"] != res_b["semantic_identity"]["fingerprint"]
    assert res_a["semantic_identity"]["source_sha256"] != res_b["semantic_identity"]["source_sha256"]
    assert res_a["task_metadata"]["task_id"] == task_a and res_b["task_metadata"]["task_id"] == task_b
    assert res_a["task_metadata"]["task_sha256"] != res_b["task_metadata"]["task_sha256"]


def test_actual_collector_canonical_order_and_empty_symbols(tmp_path):
    root_multi = tmp_path / "multi"
    multi_groups = [
        {"path": "scripts/b.py", "symbols": ["z_func", "a_func"]},
        {"path": "scripts/a.py", "symbols": ["m_func"]},
    ]
    id_multi = _make_identity(symbol_groups=multi_groups, source_manifest={"scripts/a.py": "a" * 64, "scripts/b.py": "b" * 64})
    expected_multi_json = (
        '{"gap_reference":"docs/ref#semantic","kind":"two-site-logical-work",'
        '"scenario_key":"ae00_provider_identity_tests","schema_version":1,'
        '"stage_id":"P1-00b","symbol_groups":[{"path":"scripts/a.py","symbols":["m_func"]},'
        '{"path":"scripts/b.py","symbols":["a_func","z_func"]}]}'
    )
    _make_pair(root_multi, semantic_identity=id_multi)
    assert _pm(root_multi)["work_lineage"] == {
        "schema_version": 1, "fingerprint": hashlib.sha256(expected_multi_json.encode("utf-8")).hexdigest()
    }

    root_empty = tmp_path / "empty"
    id_empty = _make_identity(symbol_groups=[])
    expected_empty_json = (
        '{"gap_reference":"docs/ref#semantic","kind":"two-site-logical-work",'
        '"scenario_key":"ae00_provider_identity_tests","schema_version":1,'
        '"stage_id":"P1-00b","symbol_groups":[]}'
    )
    _make_pair(root_empty, semantic_identity=id_empty)
    assert _pm(root_empty)["work_lineage"] == {
        "schema_version": 1, "fingerprint": hashlib.sha256(expected_empty_json.encode("utf-8")).hexdigest()
    }

    root_tampered = tmp_path / "tampered"
    tampered_id = copy.deepcopy(id_multi)
    tampered_id["symbol_groups"] = list(reversed(tampered_id["symbol_groups"]))
    _make_pair(root_tampered, semantic_identity=tampered_id)
    res_tampered = _pm(root_tampered)
    assert "work_lineage" not in res_tampered
    assert res_tampered["semantic_identity"] is None


def test_actual_legacy_and_controller_exact_work_projection(tmp_path):
    sentinel = "SECRET_DO_NOT_LEAK_BODY_TOKEN_XYZ987"
    root_leg = tmp_path / "legacy"
    _make_attempt(root_leg, "unit-worker-0", controller_run_id=None, semantic_identity=_make_identity(), prompt=sentinel)
    _make_attempt(root_leg, "unit-reviewer-0", controller_run_id=None, role="verifier", state="VERIFIED", input_worker_run_id="unit-worker-0", semantic_identity=_make_identity())
    res_leg = metrics.provider_metrics(root_leg, task_id="task", head_before=HEAD, final_worker_id="unit-worker-0", cycle_duration=50.0)

    root_ctrl = tmp_path / "ctrl"
    _make_attempt(root_ctrl, "crun-worker-0", controller_run_id=CTRL, semantic_identity=_make_identity(), prompt=sentinel)
    _make_attempt(root_ctrl, "crun-reviewer-0", controller_run_id=CTRL, role="verifier", state="VERIFIED", input_worker_run_id="crun-worker-0", semantic_identity=_make_identity())
    res_ctrl = _pm(root_ctrl)

    for res in (res_leg, res_ctrl):
        assert res["work_lineage"] == {"schema_version": 1, "fingerprint": EXPECTED_BASE_SHA}
        assert set(res["work_lineage"].keys()) == {"schema_version", "fingerprint"}
        assert set(res["semantic_identity"].keys()) == {"fingerprint", "source_sha256", "stage_id", "gap_reference", "scenario_key"}
        assert sentinel not in json.dumps(res)
        assert "symbol_groups" not in res["semantic_identity"]
        assert "source_manifest" not in res["semantic_identity"]


def test_actual_collector_task_head_gap_marker_and_identity_rejection(tmp_path):
    root_base = tmp_path / "baseline"
    _make_pair(root_base, semantic_identity=_make_identity())
    assert _pm(root_base)["work_lineage"] == {"schema_version": 1, "fingerprint": EXPECTED_BASE_SHA}

    # 1. task_id mismatch
    r = tmp_path / "bad_task"
    _make_attempt(r, "crun-worker-0", task_id="task", semantic_identity=_make_identity())
    _make_attempt(r, "crun-reviewer-0", role="verifier", state="VERIFIED", input_worker_run_id="crun-worker-0", task_id="other", semantic_identity=_make_identity())
    res = _pm(r)
    assert res["state"] == "PARTIAL" and "work_lineage" not in res

    # 2. source_head mismatch
    r = tmp_path / "bad_head"
    _make_attempt(r, "crun-worker-0", head=HEAD, semantic_identity=_make_identity())
    _make_attempt(r, "crun-reviewer-0", role="verifier", state="VERIFIED", input_worker_run_id="crun-worker-0", head="b" * 40, semantic_identity=_make_identity())
    res = _pm(r)
    assert res["state"] == "PARTIAL" and "work_lineage" not in res

    # 3. gap_reference mismatch
    r = tmp_path / "bad_gap"
    _make_pair(r, gap="docs/ref#mismatch", semantic_identity=_make_identity(gap_reference="docs/ref#semantic"))
    res = _pm(r)
    assert "work_lineage" not in res and res["semantic_identity"] is None

    # 4. duplicate marker
    r = tmp_path / "bad_dup"
    _make_pair(r, raw_goal=f"Scope\nCANONICAL_SOURCE_EXCERPTS_JSON:\n{{}}\nCANONICAL_SOURCE_EXCERPTS_JSON:\n{{}}")
    res = _pm(r)
    assert "work_lineage" not in res and res["semantic_identity"] is None

    # 5. loose marker
    r = tmp_path / "bad_loose"
    _make_pair(r, raw_goal=f"Scope CANONICAL_SOURCE_EXCERPTS_JSON: {{}}")
    res = _pm(r)
    assert "work_lineage" not in res and res["semantic_identity"] is None

    # 6. tampered fingerprint
    r = tmp_path / "bad_fp"
    _make_pair(r, semantic_identity=dict(_make_identity(), fingerprint="0" * 64))
    res = _pm(r)
    assert "work_lineage" not in res and res["semantic_identity"] is None

    # 7. tampered manifest
    r = tmp_path / "bad_mf"
    _make_pair(r, semantic_identity=dict(_make_identity(), source_manifest={"scripts/two_site_provider_metrics.py": "f" * 64}))
    res = _pm(r)
    assert "work_lineage" not in res and res["semantic_identity"] is None

    # 8. missing full identity
    r = tmp_path / "miss_id"
    _make_pair(r, semantic_identity=None)
    res = _pm(r)
    assert "work_lineage" not in res and res["semantic_identity"] is None

    # 9. worker-reviewer identity mismatch
    r = tmp_path / "diff_id"
    _make_attempt(r, "crun-worker-0", semantic_identity=_make_identity(scenario_key="scenario_worker"))
    _make_attempt(r, "crun-reviewer-0", role="verifier", state="VERIFIED", input_worker_run_id="crun-worker-0", semantic_identity=_make_identity(scenario_key="scenario_reviewer"))
    res = _pm(r)
    assert res["state"] == "PARTIAL" and "work_lineage" not in res


def test_actual_collector_link_and_byte_bound_rejection(tmp_path):
    root, outside = tmp_path / "link_test", tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    worker, _ = _make_pair(root, semantic_identity=_make_identity())
    assert _pm(root)["work_lineage"] == {"schema_version": 1, "fingerprint": EXPECTED_BASE_SHA}

    task_file = worker / "task.json"
    orig_bytes = task_file.read_bytes()

    # Symlink rejection
    outside_file = outside / "outside_task.json"
    outside_file.write_bytes(orig_bytes)
    task_file.unlink()
    task_file.symlink_to(outside_file)
    assert "work_lineage" not in _pm(root)
    assert _pm(root)["state"] == "PARTIAL"
    task_file.unlink()
    task_file.write_bytes(orig_bytes)
    assert _pm(root)["work_lineage"] == {"schema_version": 1, "fingerprint": EXPECTED_BASE_SHA}

    # Hardlink rejection
    hardlink_target = outside / "hardlink_target.json"
    os.link(task_file, hardlink_target)
    assert "work_lineage" not in _pm(root)
    assert _pm(root)["state"] == "PARTIAL"
    hardlink_target.unlink()
    task_file.unlink()
    task_file.write_bytes(orig_bytes)
    assert _pm(root)["work_lineage"] == {"schema_version": 1, "fingerprint": EXPECTED_BASE_SHA}

    # Limit+1 byte bound rejection
    oversized = orig_bytes + b" " * (600_000 - len(orig_bytes) + 1)
    task_file.write_bytes(oversized)
    res_over = _pm(root)
    assert "work_lineage" not in res_over and res_over["task_metadata"] is None
    task_file.write_bytes(orig_bytes)
    assert _pm(root)["work_lineage"] == {"schema_version": 1, "fingerprint": EXPECTED_BASE_SHA}


def test_actual_controller_failed_attempt_work_cost_and_partial_rejection(tmp_path):
    root_blocked = tmp_path / "blocked"
    _make_attempt(root_blocked, "crun-worker-0", role="worker", state="BLOCKED", usage=TOKENS, semantic_identity=_make_identity())
    res_b = _pm(root_blocked, cycle_duration=20.0)
    assert res_b["state"] == "OBSERVED_CONTROLLER_ATTEMPTS" and res_b["failed_attempts"] == 1 and res_b["first_pass"] is None
    assert res_b["usage"] == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    assert res_b["observed_usage_subtotal"] == res_b["usage"]
    assert res_b["work_lineage"] == {"schema_version": 1, "fingerprint": EXPECTED_BASE_SHA}

    root_part = tmp_path / "partial"
    _make_attempt(root_part, "crun-worker-0", role="worker", state="PROPOSAL_READY", usage=TOKENS, semantic_identity=_make_identity())
    _make_attempt(root_part, "crun-reviewer-0", role="verifier", state="VERIFIED", head="b" * 40, usage=None, with_task=False, semantic_identity=_make_identity())
    res_p = _pm(root_part)
    assert res_p["state"] == "PARTIAL" and res_p["first_pass"] is None and "work_lineage" not in res_p
    assert res_p["usage"] == {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    assert res_p["observed_usage_subtotal"] == {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    assert res_p["usage_observed_attempts"] == 1


def test_legacy_projection_and_unavailable_exact_backward_compatibility(tmp_path):
    root_leg = tmp_path / "legacy_wrap"
    attempt_path = _make_attempt(root_leg, "unit-worker-0", controller_run_id=None, semantic_identity=_make_identity())
    legacy_five = metrics._extract_semantic_identity(root_leg, attempt_path, "task", HEAD)
    assert legacy_five is not None
    assert set(legacy_five.keys()) == {"fingerprint", "source_sha256", "stage_id", "gap_reference", "scenario_key"}
    assert "work_lineage" not in legacy_five
    assert legacy_five["gap_reference"] == "docs/ref#semantic"
    assert legacy_five["stage_id"] == "P1-00b"
    assert legacy_five["scenario_key"] == "ae00_provider_identity_tests"

    unav = metrics.unavailable()
    assert set(unav.keys()) == {
        "state", "first_pass", "usage", "phase_seconds",
        "task_metadata", "semantic_identity", "session_gate", "rework_taxonomy",
    }
    assert "work_lineage" not in unav and unav["state"] == "UNAVAILABLE" and unav["first_pass"] is None
    assert unav["task_metadata"] is None and unav["semantic_identity"] is None
    assert unav["usage"] == {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    root_proj = tmp_path / "proj_only"
    projected_only = {
        "fingerprint": "a" * 64, "gap_reference": "docs/ref#semantic",
        "scenario_key": "ae00_provider_identity_tests", "source_sha256": "b" * 64, "stage_id": "P1-00b",
    }
    proj_attempt = _make_attempt(root_proj, "unit-worker-0", controller_run_id=None, semantic_identity=projected_only)
    assert metrics._extract_semantic_identity(root_proj, proj_attempt, "task", HEAD) is None
    assert metrics._extract_identity_bundle(root_proj, proj_attempt, "task", HEAD) is None
    res_proj = metrics.provider_metrics(root_proj, task_id="task", head_before=HEAD, final_worker_id="unit-worker-0", cycle_duration=50.0)
    assert "work_lineage" not in res_proj and res_proj["semantic_identity"] is None
