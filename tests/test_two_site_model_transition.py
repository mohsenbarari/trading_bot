"""A models-only commit may start fresh work, without rewriting its old CP."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess

import pytest

from scripts import two_site_codex_autopilot as autopilot
from scripts import two_site_model_transition as transition
from scripts.two_site_ai_config import AI_CONFIG_REL
from scripts.two_site_assignment_scope import AssignmentScope

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def model_change(tmp_path, monkeypatch):
    scope = AssignmentScope(assignment_id="WA-model-change", stage_id="P1-00b",
        allowed_paths=("tests/**",), forbidden_paths=("live.env",),
        expires_at="2099-01-01T00:00:00Z", sha256="f" * 64)
    old = {"head": "a" * 40, "status": "",
        "protected_refs": {ref: "d" * 40 for ref in autopilot.PROTECTED_REFS},
        "control_blobs": {path.as_posix(): "e" * 40 for path in autopilot.CONTROL_PATHS},
        "assignment_scope": scope}
    receipt = {"schema_version": "1.0", "state": "CONTINUE", "disposition": "NONE", "category": "NONE",
        "summary": "Existing accepted work", "commit": old["head"], "next_action": "Continue genuine work",
        "blockers": [], "gemini_worker_run_id": "old-worker", "gemini_reviewer_run_id": "old-reviewer",
        "proposal_sha256": "c" * 64, "head_before": "b" * 40, "head_after": old["head"], "head_changed": True}
    state = tmp_path / ".local/two-site-refactor/autopilot"; state.mkdir(parents=True, mode=0o700)
    cp_path = state / autopilot.ACCEPTED_CHECKPOINT_FILENAME
    cp_path.write_bytes(autopilot.serialize_accepted_checkpoint(receipt, old)); cp_path.chmod(0o600)
    config = tmp_path / AI_CONFIG_REL; config.parent.mkdir(parents=True)
    config.write_bytes((ROOT / AI_CONFIG_REL).read_bytes())
    current = copy.deepcopy(old); current["head"] = "1" * 40
    current["control_blobs"][AI_CONFIG_REL.as_posix()] = "2" * 40
    facts = {"current": current, "paths": AI_CONFIG_REL.as_posix() + "\0", "ancestor": True, "calls": []}
    def git(root, *args):
        assert root == tmp_path
        facts["calls"].append(args)
        if args[0] == "merge-base":
            if not facts["ancestor"]: raise subprocess.CalledProcessError(1, ["git"])
            assert args[2:] == (old["head"], facts["current"]["head"])
            return ""
        if args[0] == "diff":
            assert args[-3:] == (old["head"], facts["current"]["head"], "--")
            return facts["paths"]
        assert args[0] == "rev-parse" and args[1].startswith(old["head"] + ":")
        return old["control_blobs"][args[1].split(":", 1)[1]]
    monkeypatch.setattr(autopilot, "git", git)
    monkeypatch.setattr(autopilot, "validate_repository", lambda root, require_clean: copy.deepcopy(facts["current"]))
    checkpoint = autopilot._load_accepted_checkpoint(state)
    assert checkpoint is not None
    return tmp_path, current, checkpoint, facts, cp_path


def test_models_only_transition_reloads_real_checkpoint_and_preserves_it(model_change):
    root, current, cp, facts, path = model_change
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert transition.is_reviewed_model_config_transition(root, cp, current) is True
    assert any(call[0] == "merge-base" for call in facts["calls"])
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert json.loads(path.read_bytes())["repository"]["head"] == "a" * 40


@pytest.mark.parametrize("fault", ["business-path", "scope", "protected-ref", "unaccepted-cp",
    "control-set", "control-code", "dirty", "not-ancestor", "invalid-policy", "stale-argument", "live-drift", "old-blob"])
def test_model_admission_rejects_other_changes(model_change, monkeypatch, fault):
    root, current, cp, facts, cp_path = model_change
    if fault == "business-path": facts["paths"] += "tests/new_business.py\0"
    elif fault == "scope":
        current["assignment_scope"] = AssignmentScope(assignment_id="OTHER", stage_id="P1-00b",
            allowed_paths=("tests/**",), forbidden_paths=("live.env",),
            expires_at="2099-01-01T00:00:00Z", sha256="f" * 64)
    elif fault == "protected-ref": current["protected_refs"][next(iter(autopilot.PROTECTED_REFS))] = "3" * 40
    elif fault == "unaccepted-cp":
        value = json.loads(cp_path.read_bytes()); value["result"]["state"] = "BLOCKED"
        cp_path.write_bytes(autopilot.serialize_json(value))
    elif fault == "control-set": current["control_blobs"].pop(next(p for p in current["control_blobs"] if p != AI_CONFIG_REL.as_posix()))
    elif fault == "control-code": current["control_blobs"]["scripts/two_site_proposal_runner.py"] = "4" * 40
    elif fault == "dirty": current["status"] = " M live.env"
    elif fault == "not-ancestor": facts["ancestor"] = False
    elif fault == "invalid-policy": (root / AI_CONFIG_REL).write_text('{"schema_version":1,"roles":{}}')
    elif fault == "stale-argument": cp = copy.deepcopy(cp); cp["result"]["next_action"] = "invented replacement"
    elif fault == "live-drift": facts["current"] = copy.deepcopy(current); facts["current"]["head"] = "5" * 40
    elif fault == "old-blob":
        original = autopilot.git
        monkeypatch.setattr(autopilot, "git", lambda root, *args: "6" * 40 if args[0] == "rev-parse" else original(root, *args))
    assert transition.is_reviewed_model_config_transition(root, cp, current) is False
