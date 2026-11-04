"""Synthetic public-control transactions and real strict SDK replay; no model calls."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scripts import two_site_qoder_authority as a
from scripts import two_site_ai_config as ai
from scripts import two_site_codex_autopilot as ap
from scripts import two_site_assignment_scope as scopes
from scripts import two_site_qoder_driver as driver

POLICY = Path(__file__).resolve().parents[1] / a.POLICY_REL
REAL_RENEWAL_OBSERVER = a._renewal_observed_repository
REAL_ACTIVE_LOADER = scopes.load_active_assignment_scope

OLD = "WA-TEST-OLD"
NEW = "WA-TEST-RENEWED"
BODY = "\n\n## Accepted history\n\nCODEX_AUTHORED is historical. Preserve every byte: فارسی.\n"


def document(expires):
    return ("# Original history title\n\n```yaml\n" + yaml.safe_dump({
        "id": OLD, "status": "READY", "role": "WORKER", "stage_id": "P1-00b",
        "integration_branch": "refactor/two-site-architecture", "external_access": "NONE",
        "expires_at": expires, "parent_assignment_id": "WA-PARENT",
        "original_parent_assignment_id": "WA-ORIGINAL", "allowed_paths": ["tests/**", "docs/**"],
        "forbidden_paths": [".local/**", "main.py"],
    }, sort_keys=False) + "```" + BODY).encode()


def controls(now):
    policy = json.loads(POLICY.read_text())
    expiry = (now + timedelta(hours=1)).isoformat()
    old = document(expiry)
    registry = yaml.safe_dump({"schema_version": "1.0", "updated_at": "historical",
        "integration": {"path": "/synthetic/integration", "branch": "refactor/two-site-architecture",
            "status": "ACTIVE_PROPOSAL_REFACTOR", "active_assignment_id": OLD,
            "owner_role": "CODEX_COORDINATOR", "head_sha": "historical-do-not-rewrite"},
        "stage_worktrees": [{"status": "REMOVED_AFTER_CLOSE", "assignment_id": "LEGACY"}]},
        sort_keys=False).encode()
    return policy, old, registry, (now + timedelta(days=2)).isoformat()


def test_lossless_body_parent_and_removed_registry_history():
    now = datetime.now(timezone.utc); policy, old, reg, expiry = controls(now)
    new, after, previous, current = a.renewal_documents(old, reg, old_id=OLD, new_id=NEW,
        new_expiry=expiry, reason="same scope finite renewal", now=now, policy=policy)
    assert old.endswith(BODY.encode()) and new.endswith(BODY.encode())
    before = yaml.safe_load(reg); changed = yaml.safe_load(after)
    assert changed["stage_worktrees"] == before["stage_worktrees"]
    assert changed["integration"] == {**before["integration"], "active_assignment_id": NEW}
    h = scopes._safe_load_yaml(new.decode().split("```yaml\n")[1].split("```", 1)[0])
    assert h["parent_assignment_id"] == OLD and h["renewal_previous_parent_assignment_id"] == "WA-PARENT"
    assert h["original_parent_assignment_id"] == "WA-ORIGINAL"
    assert current.allowed_paths == previous.allowed_paths and current.forbidden_paths == previous.forbidden_paths
    assert current.stage_id == previous.stage_id == "P1-00b"
    assert old == document((now + timedelta(hours=1)).isoformat())


@pytest.mark.parametrize("change", ["too-early", "expired", "too-long", "not-extension", "same-id", "traversal", "reason-control"])
def test_renewal_refuses_invalid_window_expiry_and_identity(change):
    now = datetime.now(timezone.utc); policy, old, reg, expiry = controls(now)
    new_id, reason = NEW, "valid"
    if change == "too-early": now -= timedelta(days=2)
    if change == "expired": now += timedelta(hours=2)
    if change == "too-long": expiry = (now + timedelta(days=9)).isoformat()
    if change == "not-extension": expiry = now.isoformat()
    if change == "same-id": new_id = OLD
    if change == "traversal": new_id = "../escape"
    if change == "reason-control": reason = "invalid\x00reason"
    with pytest.raises(a.AuthorityLifecycleError):
        a.renewal_documents(old, reg, old_id=OLD, new_id=new_id, new_expiry=expiry, reason=reason, now=now, policy=policy)


@pytest.mark.parametrize("old_change", ["external_access: NONE", "role: WORKER", "status: READY"])
def test_renewal_requires_existing_valid_nonexternal_worker_contract(old_change):
    now = datetime.now(timezone.utc); policy, old, reg, expiry = controls(now)
    with pytest.raises((a.AuthorityLifecycleError, scopes.AssignmentScopeError)):
        a.renewal_documents(old.replace(old_change.encode(), b"external_access: SSH"), reg,
            old_id=OLD, new_id=NEW, new_expiry=expiry, reason="valid", now=now, policy=policy)


def test_second_renewal_keeps_full_chain_and_old_body():
    now = datetime.now(timezone.utc); policy, old, reg, expiry = controls(now)
    one, reg1, _, current = a.renewal_documents(old, reg, old_id=OLD, new_id=NEW,
        new_expiry=expiry, reason="first", now=now, policy=policy)
    at = datetime.fromisoformat(expiry) - timedelta(hours=1)
    two, reg2, _, future = a.renewal_documents(one, reg1, old_id=NEW, new_id="WA-TEST-THIRD",
        new_expiry=(at + timedelta(days=2)).isoformat(), reason="second", now=at, policy=policy)
    assert two.endswith(BODY.encode()) and one.endswith(BODY.encode())
    header = scopes._safe_load_yaml(two.decode().split("```yaml\n")[1].split("```", 1)[0])
    assert header["parent_assignment_id"] == NEW and header["renewal_previous_parent_assignment_id"] == OLD
    assert future.allowed_paths == current.allowed_paths


@pytest.fixture
def job(tmp_path, monkeypatch):
    root = tmp_path / "integration"; root.mkdir(); shared = tmp_path / "shared"; shared.mkdir()
    now = datetime.now(timezone.utc) - timedelta(seconds=2)
    policy, old, reg, expiry = controls(now)
    new, after, previous, current = a.renewal_documents(old, reg, old_id=OLD, new_id=NEW,
        new_expiry=expiry, reason="test renewal", now=now, policy=policy)
    old_rel = Path(".local/two-site-refactor/assignments") / (OLD + ".md")
    new_rel = old_rel.with_name(NEW + ".md")
    (shared / old_rel).parent.mkdir(parents=True)
    (shared / old_rel).write_bytes(old); (shared / a.REGISTRY).write_bytes(reg)
    before = {"head": "1" * 40, "status": "", "protected_refs": {"main": "2" * 40},
        "control_blobs": {"fixture": "3" * 40}, "assignment_scope": ap.assignment_scope_context(previous)}
    cp = {"repository": copy.deepcopy(before), "result": {"state": "CONTINUE"}}
    plan = {"schema_version": 1, "kind": a.KIND, "repository": before, "prior_checkpoint_sha256": "4" * 64,
        "policy_sha256": "5" * 64, "created_at": now.isoformat(), "new_id": NEW, "new_expiry": expiry,
        "reason": "test renewal", "old_assignment_path": old_rel.as_posix(), "new_assignment_path": new_rel.as_posix(),
        "old_assignment": old.decode(), "new_assignment": new.decode(), "old_registry": reg.decode(),
        "new_registry": after.decode(), "new_scope": ap.assignment_scope_context(current)}
    directory = root / a.JOBS / previous.sha256; directory.mkdir(mode=0o700, parents=True)
    a.write_new(directory / "intent.json", a.encode(plan))
    monkeypatch.setattr(a, "load_policy", lambda r: (policy, "5" * 64))
    monkeypatch.setattr(a, "checkpoint", lambda r: (copy.deepcopy(cp), "4" * 64))
    monkeypatch.setattr(a, "shared_checkout", lambda r: shared)
    def active(_):
        data = yaml.safe_load((shared / a.REGISTRY).read_text())
        ident = data["integration"]["active_assignment_id"]
        raw = (shared / old_rel.with_name(ident + ".md")).read_bytes()
        return scopes.parse_assignment_scope_bytes(raw, expected_assignment_id=ident,
            integration_branch="refactor/two-site-architecture")
    def repo(_):
        result = copy.deepcopy(before); result["assignment_scope"] = ap.assignment_scope_context(active(root)); return result
    monkeypatch.setattr(a, "repository", repo)
    monkeypatch.setattr(a, "_renewal_observed_repository", lambda r, plan: a.repository(r))
    monkeypatch.setattr(scopes, "load_active_assignment_scope", active)
    settings = {"provider": "qoder", "model": "synthetic-qoder", "reasoning_effort": "high"}
    binary = root / "fake-qoder-pin"; binary.write_bytes(b"SYNTHETIC_NOT_EXECUTED"); binary.chmod(0o400)
    runtime = {"path": str(binary), "version": "synthetic", "sha256": a.digest(binary.read_bytes())}
    monkeypatch.setattr(ai, "role_settings", lambda r, role: settings)
    monkeypatch.setattr(ai, "load_runtime_config", lambda r: {"coordinator": runtime})
    def review(role, session):
        d = directory / role; d.mkdir(mode=0o700)
        a.write_new(d / "plan.json", a.encode(plan))
        result = {"decision": "APPROVE", "findings": [], "proposal_sha256": a.digest(a.encode(plan)), "source_head": before["head"]}
        events = [{"type": "system", "subtype": "init", "session_id": session, "model": settings["model"],
            "tools": ["mcp_list"], "mcp_servers": [], "plugins": []},
            {"type": "assistant", "session_id": session, "message": {"role": "assistant", "content": [{"type": "text", "text": "synthetic review"}]}},
            {"type": "result", "subtype": "success", "is_error": False, "session_id": session, "num_turns": 1,
             "stop_reason": "end_turn", "permission_denials": [], "result": json.dumps(result), "usage": {"input_tokens": 0, "output_tokens": 0}}]
        raw = "".join(json.dumps(e) + "\n" for e in events).encode()
        a.write_new(d / "stream.jsonl", raw)
        a.write_new(d / "prompt.txt", a._review_prompt(plan).encode())
        a.write_new(d / "capture.json", a.encode({"normal_exit": True, "exit_code": 0, "cli": {**runtime, "provider": "qoder", "logged_in": True,
            "auto_update_enabled": False, "model": settings["model"], "reasoning": settings["reasoning_effort"]},
            "role": "coordinator" if role == "planner" else "final_reviewer", "settings": settings,
            "proposal_sha256": a.digest(a.encode(plan)), "source_head": before["head"],
            "reviewed_at": (now + timedelta(seconds=1)).isoformat(), "stream_sha256": a.digest(raw), "prompt_sha256": a.digest(a._review_prompt(plan).encode())}))
    review("planner", "synthetic-author"); review("reviewer", "synthetic-reviewer")
    return root, shared, directory, plan, cp, repo


def test_exact_publication_crash_replay_and_startup_preserve_old_cpp(job):
    root, shared, directory, plan, cp, repo = job
    original = copy.deepcopy(cp)
    result = a.complete_renewal_locked(root, directory)
    assert result["kind"] == a.KIND and result["old_scope"] == cp["repository"]["assignment_scope"]
    assert (shared / plan["old_assignment_path"]).read_text() == plan["old_assignment"]
    assert (shared / plan["new_assignment_path"]).read_text() == plan["new_assignment"]
    assert a.complete_renewal_locked(root, directory) == result
    assert a.validate_authority_transition_for_startup(root, cp, repo(root)) == result
    assert cp == original  # real adapter never writes the genuine CPP


@pytest.mark.parametrize("point", ["file-only", "registry-only", "temp-only"])
def test_resume_exact_intermediate_states(job, point):
    root, shared, directory, plan, cp, repo = job
    if point in {"file-only", "registry-only"}:
        a.write_new(shared / plan["new_assignment_path"], plan["new_assignment"].encode())
    if point == "registry-only": (shared / a.REGISTRY).write_text(plan["new_registry"])
    if point == "temp-only":
        temp = (shared / a.REGISTRY).with_name(".authority-registry-" + a.digest(plan["new_registry"].encode()) + ".tmp")
        a.write_new(temp, plan["new_registry"].encode())
    result = a.complete_renewal_locked(root, directory)
    assert result["new_scope"]["assignment_id"] == NEW
    assert cp["repository"]["assignment_scope"]["assignment_id"] == OLD


@pytest.mark.parametrize("tamper", ["registry", "old-file", "new-file", "body", "extension", "paths", "review-stream", "review-prompt", "same-session", "model", "policy", "cpp", "source"])
def test_foreign_state_or_proof_never_grants_authority(job, monkeypatch, tamper):
    root, shared, directory, plan, cp, repo = job
    if tamper == "registry": (shared / a.REGISTRY).write_text("foreign: true\n")
    if tamper == "old-file": (shared / plan["old_assignment_path"]).write_text(plan["old_assignment"] + "foreign")
    if tamper == "new-file": a.write_new(shared / plan["new_assignment_path"], b"foreign")
    if tamper in {"body", "extension", "paths"}:
        if tamper == "body": plan["new_assignment"] += "FOREIGN_BODY"
        if tamper == "paths": plan["new_assignment"] = plan["new_assignment"].replace("tests/**", "api/**")
        if tamper == "extension":
            expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
            plan["new_assignment"] = plan["new_assignment"].replace(plan["new_expiry"], expiry)
            plan["new_expiry"] = expiry
        changed_scope = scopes.parse_assignment_scope_bytes(plan["new_assignment"].encode(),
            expected_assignment_id=NEW, integration_branch="refactor/two-site-architecture")
        plan["new_scope"] = ap.assignment_scope_context(changed_scope)
        (directory / "intent.json").write_bytes(a.encode(plan))
        # Even synthetic fresh approvals of an invalid draft cannot widen host authority.
        for role in ("planner", "reviewer"):
            (directory / role / "plan.json").write_bytes(a.encode(plan))
            p = directory / role / "stream.jsonl"
            events = [json.loads(line) for line in p.read_text().splitlines()]
            value = json.loads(events[-1]["result"]); value["proposal_sha256"] = a.digest(a.encode(plan))
            events[-1]["result"] = json.dumps(value)
            p.write_text("".join(json.dumps(e) + "\n" for e in events))
            q = directory / role / "capture.json"; v = a.decode(q.read_bytes())
            v["proposal_sha256"] = a.digest(a.encode(plan)); v["stream_sha256"] = a.digest(p.read_bytes()); v["prompt_sha256"] = a.digest(a._review_prompt(plan).encode())
            (directory / role / "prompt.txt").write_text(a._review_prompt(plan))
            q.write_bytes(a.encode(v))
    if tamper == "review-prompt": (directory / "reviewer/prompt.txt").write_text("not the full reviewed proposal")
    if tamper == "review-stream":
        p = directory / "reviewer/stream.jsonl"; p.write_bytes(p.read_bytes() + b"{}\n")
    if tamper == "same-session":
        p = directory / "reviewer/stream.jsonl"; p.write_bytes(p.read_bytes().replace(b"synthetic-reviewer", b"synthetic-author"))
        q = directory / "reviewer/capture.json"; v = a.decode(q.read_bytes()); v["stream_sha256"] = a.digest(p.read_bytes()); q.write_bytes(a.encode(v))
    if tamper == "model": monkeypatch.setattr(ai, "role_settings", lambda *args: {"provider": "qoder", "model": "other", "reasoning_effort": "high"})
    if tamper == "policy": monkeypatch.setattr(a, "load_policy", lambda r: ({}, "0" * 64))
    if tamper == "cpp": monkeypatch.setattr(a, "checkpoint", lambda r: (cp, "0" * 64))
    if tamper == "source": monkeypatch.setattr(a, "repository", lambda r: {**repo(r), "head": "9" * 40})
    with pytest.raises((a.AuthorityLifecycleError, scopes.AssignmentScopeError, driver.QoderError)):
        a.complete_renewal_locked(root, directory)
    assert cp["repository"]["assignment_scope"]["assignment_id"] == OLD


def _schema_value(schema):
    if "const" in schema: return schema["const"]
    if "enum" in schema: return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list): kind = next(k for k in kind if k != "null")
    if kind == "object": return {k: _schema_value(schema["properties"][k]) for k in schema.get("required", [])}
    if kind == "array": return [_schema_value(schema["items"])] * schema.get("minItems", 0)
    if kind == "boolean": return True
    if schema.get("format") == "date-time": return datetime.now(timezone.utc).isoformat()
    if "64" in schema.get("pattern", ""): return "a" * 64
    if "40" in schema.get("pattern", ""): return "a" * 40
    return "synthetic"


@pytest.fixture
def closed_stage(tmp_path, monkeypatch):
    """Actual disposable Git closeout + real wrapper and strict SDK replay; synthetic models only."""
    import shutil
    import subprocess
    project = Path(__file__).resolve().parents[1]
    main = tmp_path / "main"; main.mkdir()
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(tmp_path)}
    def git(root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], env=env)
    git(main, "init", "-q"); git(main, "config", "user.name", "Synthetic"); git(main, "config", "user.email", "fixture@example.invalid")
    for rel in (a.POLICY_REL, ai.AI_CONFIG_REL, Path("scripts/two_site_codex_closeout_review.sh"),
        Path("docs/refactor/two-server/templates/final-review.schema.json"),
        Path("docs/refactor/two-server/templates/closeout-review.schema.json"),
        Path("docs/refactor/two-server/MASTER_PLAN.md"), Path("docs/refactor/two-server/EXECUTION_GOVERNANCE.md")):
        dst = main / rel; dst.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(project / rel, dst)
    (main / ".gitignore").write_text("/.local/\n")
    docs = main / "docs/refactor/two-server"; (docs / "CHANGE_LEDGER.md").write_text("# synthetic ledger\n")
    before_order = "design_status: APPROVED\nstages:\n  P1-00: {state: COMPLETE, depends_on: [], production: false}\n  P1-00b: {state: IN_PROGRESS, depends_on: [P1-00], production: false}\n  P1-01: {state: PROPOSED, depends_on: [P1-00, P1-00b], production: false}\n"
    (docs / "execution-order.yaml").write_text(before_order)
    git(main, "add", "."); git(main, "commit", "-qm", "synthetic accepted Stage")
    accepted = git(main, "rev-parse", "HEAD").decode().strip()
    root = main / ".local/two-site-refactor/integration"
    root.parent.mkdir(parents=True)
    git(main, "worktree", "add", "-qb", "refactor/two-site-architecture", str(root), "HEAD")
    old = document((datetime.now(timezone.utc) + timedelta(hours=2)).isoformat())
    shared = main; assignment = shared / ".local/two-site-refactor/assignments" / (OLD + ".md")
    assignment.parent.mkdir(parents=True); assignment.write_bytes(old)
    registry = {"schema_version": "1.0", "updated_at": "historical", "integration": {"path": str(root),
        "branch": "refactor/two-site-architecture", "status": "ACTIVE_PROPOSAL_REFACTOR", "active_assignment_id": OLD,
        "owner_role": "CODEX_COORDINATOR"}, "stage_worktrees": [{"status": "REMOVED_AFTER_CLOSE"}]}
    (shared / a.REGISTRY).write_text(yaml.safe_dump(registry, sort_keys=False))
    bundle = Path(".local/two-site-refactor/review-bundles") / ("P1-00b-" + OLD)
    (root / bundle).mkdir(parents=True)
    fschema = json.loads((root / "docs/refactor/two-server/templates/final-review.schema.json").read_text())
    final = _schema_value(fschema)
    final.update(stage_id="P1-00b", assignment_id=OLD, integration_base_sha=accepted, reviewed_head_sha=accepted,
        reviewed_at_utc=datetime.now(timezone.utc).isoformat(), verdict="APPROVE", findings=[],
        verification=[{"check": "synthetic complete", "result": "PASS", "evidence_uri": "fixture"}])
    final["scope"].update(unauthorized_external_mutation="NONE")
    final["decision"].update(accepted_commit=accepted, conditions=[])
    durable = root / "docs/refactor/two-server/reviews" / ("P1-00b-" + OLD)
    durable.parent.mkdir(); durable.with_suffix(".json").write_text(json.dumps(final, sort_keys=True) + "\n")
    durable.with_suffix(".md").write_text("# synthetic full Stage approval\n")
    (root / "docs/refactor/two-server/CHANGE_LEDGER.md").write_text("# synthetic ledger\nP1-00b " + accepted + " approved\n")
    (root / "docs/refactor/two-server/execution-order.yaml").write_text(before_order.replace("P1-00b: {state: IN_PROGRESS", "P1-00b: {state: COMPLETE"))
    git(root, "add", "."); git(root, "commit", "-qm", "synthetic exact four-file closeout")
    closed = git(root, "rev-parse", "HEAD").decode().strip()
    patch_hash = hashlib.sha256(git(root, "diff", "--binary", "--full-index", "--no-ext-diff", accepted, closed)).hexdigest()
    cschema = json.loads((root / "docs/refactor/two-server/templates/closeout-review.schema.json").read_text())
    closeout = _schema_value(cschema)
    closeout.update(stage_id="P1-00b", assignment_id=OLD, accepted_stage_sha=accepted,
        proposed_patch_sha256=patch_hash, reviewed_at_utc=datetime.now(timezone.utc).isoformat(), verdict="APPROVE", findings=[])
    closeout["scope"].update(unauthorized_external_mutation="NONE")
    closeout["decision"].update(commit_authorized=True, conditions=[])
    model_raw = (root / ai.AI_CONFIG_REL).read_bytes(); selected = json.loads(model_raw)["roles"]["final_reviewer"]
    def proof(receipt, filename, schema_name, session, token):
        raw = (json.dumps(receipt, sort_keys=True) + "\n").encode(); target = root / bundle / filename; target.write_bytes(raw)
        run_rel = Path(".local/two-site-refactor/review-bridge/runs") / (token * 32); run = root / run_rel; run.mkdir(mode=0o700, parents=True)
        events = [{"type": "system", "subtype": "init", "session_id": session, "model": selected["model"],
            "tools": ["mcp_list"], "mcp_servers": [], "plugins": []},
            {"type": "result", "subtype": "success", "is_error": False, "session_id": session,
             "num_turns": 1, "stop_reason": "end_turn", "permission_denials": [], "result": json.dumps(receipt)}]
        stream = "".join(json.dumps(e) + "\n" for e in events).encode()
        prompt = ("SYNTHETIC COMPLETE REVIEW PROMPT " + filename).encode()
        a.write_new(run / "prompt.txt", prompt); a.write_new(run / "stream.jsonl", stream); a.write_new(run / "result.json", raw)
        execution = {"schema": "qoder-schema-review-execution/1", "provider": "qoder", "role": "final_reviewer",
            "normal_exit_observed": True, "exit_code": 0,
            "run_dir": run_rel.as_posix(), "model_config": selected, "session_id": session,
            "prompt_sha256": a.digest(prompt),
            "stream_sha256": a.digest(stream), "result_sha256": a.digest(raw),
            "review_schema_sha256": a.digest((root / "docs/refactor/two-server/templates" / schema_name).read_bytes()),
            "model_policy_sha256": a.digest(model_raw)}
        (run / "execution.json").write_bytes(a.encode(execution))
        target.with_suffix(".execution.json").write_bytes(a.encode({**execution,
            "published_receipt_sha256": a.digest(raw)}))
    proof(final, "CODEX_FINAL_REVIEW.json", "final-review.schema.json", "synthetic-final", "a")
    proof(closeout, "CODEX_CLOSEOUT_PREACTION.json", "closeout-review.schema.json", "synthetic-closeout", "b")
    historical = scopes.parse_assignment_scope_bytes(old, expected_assignment_id=OLD, integration_branch="refactor/two-site-architecture")
    cp = {"repository": {"head": accepted, "assignment_scope": ap.assignment_scope_context(historical),
        "protected_refs": ap.ref_snapshot(root)}, "result": {"state": "CONTINUE"}}
    monkeypatch.setattr(a, "checkpoint", lambda _: (cp, "c" * 64))
    policy_raw = (root / a.POLICY_REL).read_bytes()
    context = {"kind": "QODER_POST_CLOSEOUT_REVIEW_V1", "source_head": closed, "policy_sha256": a.digest(policy_raw),
        "checkpoint_sha256": "c" * 64, "registry_sha256": a.digest((shared / a.REGISTRY).read_bytes()),
        "assignment_id": OLD, "assignment_sha256": a.digest(old), "stage_id": "P1-00b",
        "accepted_stage_head": accepted, "closeout_bundle": bundle.as_posix()}
    return root, shared, context, git


def test_real_closeout_wrapper_and_sdk_context_keep_old_scope_historical(closed_stage):
    root, shared, context, _ = closed_stage
    controls = a.validate_authority_review_context(root, context)
    assert controls == (shared / a.REGISTRY, shared / ".local/two-site-refactor/assignments" / (OLD + ".md"))
    with pytest.raises(scopes.AssignmentScopeError, match="IN_PROGRESS"):
        scopes.load_active_assignment_scope(root)  # ordinary business remains rejected


@pytest.mark.parametrize("field", ["source_head", "policy_sha256", "checkpoint_sha256", "registry_sha256", "assignment_sha256", "closeout_bundle"])
def test_authority_context_refuses_forged_or_drifted_bindings(closed_stage, field):
    root, shared, context, _ = closed_stage
    changed = copy.deepcopy(context); changed[field] = "f" * 40 if field == "source_head" else "forged"
    with pytest.raises(a.AuthorityLifecycleError): a.validate_authority_review_context(root, changed)


def test_authority_context_requires_real_closeout_not_boolean_or_approval_text(closed_stage):
    root, shared, context, _ = closed_stage
    with pytest.raises(a.AuthorityLifecycleError): a.validate_authority_review_context(root, {"approved": True})
    p = root / context["closeout_bundle"] / "CODEX_CLOSEOUT_PREACTION.execution.json"
    value = json.loads(p.read_text()); value["normal_exit_observed"] = False; p.write_bytes(a.encode(value))
    with pytest.raises(a.AuthorityLifecycleError, match="REVIEW_PROOF"):
        a.validate_authority_review_context(root, context)


@pytest.mark.parametrize("tamper", ["prompt", "run-execution", "published-sha"])
def test_authority_context_replays_exact_prompt_run_proof_and_publication(closed_stage, tamper):
    root, _, context, _ = closed_stage
    sidecar = root / context["closeout_bundle"] / "CODEX_CLOSEOUT_PREACTION.execution.json"
    proof = a.decode(sidecar.read_bytes()); run = root / proof["run_dir"]
    if tamper == "prompt":
        (run / "prompt.txt").write_bytes((run / "prompt.txt").read_bytes() + b"drift")
    elif tamper == "run-execution":
        value = a.decode((run / "execution.json").read_bytes()); value["session_id"] += "-drift"
        (run / "execution.json").write_bytes(a.encode(value))
    else:
        proof["published_receipt_sha256"] = "f" * 64; sidecar.write_bytes(a.encode(proof))
    with pytest.raises(a.AuthorityLifecycleError, match="REVIEW_(PROOF|CUSTODY|PUBLICATION)"):
        a.validate_authority_review_context(root, context)


def test_next_stage_draft_checks_dependencies_exact_patch_and_protected_scope(closed_stage):
    root, shared, context, _ = closed_stage
    policy = json.loads((root / a.POLICY_REL).read_text())
    draft = yaml.safe_load(document((datetime.now(timezone.utc) + timedelta(days=3)).isoformat()).decode().split("```yaml\n")[1].split("```", 1)[0])
    draft.update(id="WA-NEXT", stage_id="P1-01", parent_assignment_id=OLD,
        forbidden_paths=policy["forbidden_scope_patterns"])
    raw = ("# scope\n```yaml\n" + yaml.safe_dump(draft, sort_keys=False) + "```\n").encode()
    plan = a.prepare_next_stage_plan(root, context, "P1-01", raw)
    assert plan["kind"] == "QODER_NEXT_STAGE_SCOPE_REVIEW_V1"
    before, after = plan["execution_order_before"], plan["execution_order_after"]
    assert after == before.replace("P1-01: {state: PROPOSED", "P1-01: {state: IN_PROGRESS")
    assert plan["full_stage_card"].startswith("## `P1-01`")
    assert plan["closeout_post_commit_verified"] is True
    assert "P1-01: {state: PROPOSED" in (root / "docs/refactor/two-server/execution-order.yaml").read_text()
    widened = copy.deepcopy(draft); widened["forbidden_paths"] = []
    bad = ("```yaml\n" + yaml.safe_dump(widened) + "```\n").encode()
    with pytest.raises(a.AuthorityLifecycleError): a.prepare_next_stage_plan(root, context, "P1-01", bad)


@pytest.fixture
def reviewed_stage(closed_stage, monkeypatch):
    """Real Git effects and hooks, strict synthetic independent Qoder reviews."""
    from contextlib import nullcontext
    root, shared, context, git = closed_stage
    policy = json.loads((root / a.POLICY_REL).read_text())
    header = yaml.safe_load(document((datetime.now(timezone.utc) + timedelta(days=3)).isoformat()).decode().split("```yaml\n")[1].split("```", 1)[0])
    header.update(id="WA-NEXT-STAGE", stage_id="P1-01", parent_assignment_id=OLD,
        forbidden_paths=policy["forbidden_scope_patterns"])
    scope_raw = ("# independently reviewed next Stage\n```yaml\n" + yaml.safe_dump(header, sort_keys=False) + "```\n").encode()
    plan = a.prepare_next_stage_plan(root, context, "P1-01", scope_raw)
    directory = root / a.JOBS / ("stage-" + a.digest(a.encode(plan))); directory.mkdir(mode=0o700, parents=True)
    a.write_new(directory / "plan.json", a.encode(plan))
    settings = json.loads((root / ai.AI_CONFIG_REL).read_text())["roles"]["final_reviewer"]
    binary = root / ".local/synthetic-qoder-pin"; binary.write_bytes(b"SYNTHETIC_NEVER_EXECUTED"); binary.chmod(0o400)
    runtime = {"path": str(binary), "version": "synthetic", "sha256": a.digest(binary.read_bytes())}
    monkeypatch.setattr(ai, "role_settings", lambda r, role: settings)
    monkeypatch.setattr(ai, "load_runtime_config", lambda r: {"coordinator": runtime})
    for name, role, session in (("planner", "coordinator", "synthetic-next-planner"), ("reviewer", "final_reviewer", "synthetic-next-reviewer")):
        run = directory / name; run.mkdir(mode=0o700)
        a.write_new(run / "plan.json", a.encode(plan))
        result = {"decision": "APPROVE", "findings": [], "proposal_sha256": a.digest(a.encode(plan)), "source_head": context["source_head"]}
        stream = (json.dumps({"type": "system", "subtype": "init", "session_id": session, "model": settings["model"], "tools": ["mcp_list"], "mcp_servers": [], "plugins": []}) + "\n" +
            json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": session, "num_turns": 1, "stop_reason": "end_turn", "permission_denials": [], "result": json.dumps(result)}) + "\n").encode()
        a.write_new(run / "stream.jsonl", stream)
        a.write_new(run / "prompt.txt", a._review_prompt(plan).encode())
        a.write_new(run / "capture.json", a.encode({"normal_exit": True, "exit_code": 0,
            "cli": {**runtime, "provider": "qoder", "logged_in": True, "auto_update_enabled": False, "model": settings["model"], "reasoning": settings["reasoning_effort"]},
            "role": role, "settings": settings, "proposal_sha256": a.digest(a.encode(plan)), "source_head": context["source_head"],
            "reviewed_at": datetime.now(timezone.utc).isoformat(), "stream_sha256": a.digest(stream), "prompt_sha256": a.digest(a._review_prompt(plan).encode())}))
    cp = copy.deepcopy(a.checkpoint(root)[0])
    monkeypatch.setattr(a, "authority_holds", lambda r: nullcontext())  # effect tests own this disposable fixture exclusively
    def repo(_):
        active = scopes.load_active_assignment_scope(root)
        return {"head": git(root, "rev-parse", "HEAD").decode().strip(), "status": "",
            "protected_refs": ap.ref_snapshot(root), "control_blobs": {"synthetic": "0" * 40},
            "assignment_scope": ap.assignment_scope_context(active)}
    monkeypatch.setattr(a, "repository", repo)
    return root, shared, context, git, directory, plan, cp, repo


def test_stage_open_publication_real_git_hooks_single_parent_scope_and_startup(reviewed_stage):
    root, shared, context, git, directory, plan, cp, repo = reviewed_stage
    old_cp = copy.deepcopy(cp); old_assignment = (shared / ".local/two-site-refactor/assignments" / (OLD + ".md")).read_bytes()
    hook = shared / ".git/hooks/pre-commit"; hook.write_text("#!/bin/sh\nprintf ran > .local/hook-observed\n"); hook.chmod(0o700)
    result = a.publish_reviewed_next_stage(root, directory)
    assert (root / ".local/hook-observed").read_text() == "ran"
    head = git(root, "rev-parse", "HEAD").decode().strip()
    assert git(root, "rev-list", "--parents", "-n", "1", head).decode().split() == [head, context["source_head"]]
    assert git(root, "diff", "--name-only", context["source_head"], head).decode().splitlines() == [a.ORDER_PATH]
    assert scopes.load_active_assignment_scope(root).stage_id == "P1-01"
    assert (shared / ".local/two-site-refactor/assignments" / (OLD + ".md")).read_bytes() == old_assignment
    assert cp == old_cp and cp["repository"]["head"] != head
    bridge = a.validate_authority_transition_for_startup(root, cp, repo(root))
    assert bridge["kind"] == a.STAGE_PUBLICATION and bridge["new_repository"] == result["new_repository"]


@pytest.mark.parametrize("fault", ["source-temp", "after-patch", "after-commit", "after-assignment", "after-registry"])
def test_stage_open_exact_interrupted_effects_resume_without_recommit(reviewed_stage, monkeypatch, fault):
    root, shared, context, git, directory, plan, cp, repo = reviewed_stage
    real_commit, real_new, real_cas, real_replace = a._commit_stage_open, a.write_new, a._cas_registry, a.os.replace
    def commit(*args):
        if fault == "after-patch": raise RuntimeError("synthetic interruption")
        real_commit(*args)
        if fault == "after-commit": raise RuntimeError("synthetic interruption")
    def new(path, raw):
        real_new(path, raw)
        if fault == "after-assignment" and Path(path).name == "WA-NEXT-STAGE.md": raise RuntimeError("synthetic interruption")
    def cas(*args):
        real_cas(*args)
        if fault == "after-registry": raise RuntimeError("synthetic interruption")
    def replace(source, target):
        if fault == "source-temp" and str(target).endswith(a.ORDER_PATH): raise RuntimeError("synthetic interruption")
        return real_replace(source, target)
    monkeypatch.setattr(a.os, "replace", replace)
    monkeypatch.setattr(a, "_commit_stage_open", commit); monkeypatch.setattr(a, "write_new", new); monkeypatch.setattr(a, "_cas_registry", cas)
    with pytest.raises(RuntimeError, match="interruption"): a.publish_reviewed_next_stage(root, directory)
    prior = git(root, "rev-parse", "HEAD").decode().strip()
    monkeypatch.setattr(a.os, "replace", real_replace)
    monkeypatch.setattr(a, "_commit_stage_open", real_commit); monkeypatch.setattr(a, "write_new", real_new); monkeypatch.setattr(a, "_cas_registry", real_cas)
    result = a.resume_pending_next_stage(root, cp["repository"]["assignment_scope"]["sha256"])
    assert result["new_scope"]["stage_id"] == "P1-01"
    if fault not in {"source-temp", "after-patch"}: assert result["source_commit"] == prior
    assert a.resume_pending_next_stage(root, cp["repository"]["assignment_scope"]["sha256"]) == result


def test_stage_open_hook_rejection_is_not_bypassed_and_exact_resume_keeps_evidence(reviewed_stage):
    root, shared, context, git, directory, plan, cp, repo = reviewed_stage
    hook = shared / ".git/hooks/pre-commit"; hook.write_text("#!/bin/sh\nexit 1\n"); hook.chmod(0o700)
    with pytest.raises(a.AuthorityLifecycleError, match="GIT_COMMIT_FAILED"):
        a.publish_reviewed_next_stage(root, directory)
    assert git(root, "rev-parse", "HEAD").decode().strip() == context["source_head"]
    assert yaml.safe_load((shared / a.REGISTRY).read_text())["integration"]["active_assignment_id"] == OLD
    hook.write_text("#!/bin/sh\nexit 0\n")
    assert a.resume_pending_next_stage(root, cp["repository"]["assignment_scope"]["sha256"])["kind"] == a.STAGE_PUBLICATION


@pytest.mark.parametrize("tamper", ["source", "index", "registry", "assignment", "review", "same-session", "cpp", "stop"])
def test_stage_publication_never_admits_foreign_partial_state(reviewed_stage, monkeypatch, tamper):
    root, shared, context, git, directory, plan, cp, repo = reviewed_stage
    real = a._commit_stage_open
    monkeypatch.setattr(a, "_commit_stage_open", lambda *args: (_ for _ in ()).throw(RuntimeError("after patch")))
    with pytest.raises(RuntimeError): a.publish_reviewed_next_stage(root, directory)
    monkeypatch.setattr(a, "_commit_stage_open", real)
    if tamper == "source": (root / a.ORDER_PATH).write_text(plan["execution_order_after"] + "# foreign\n")
    if tamper == "index":
        p = root / "docs/refactor/two-server/MASTER_PLAN.md"; p.write_text(p.read_text() + "\nforeign\n"); git(root, "add", "--", str(p))
    if tamper == "registry": (shared / a.REGISTRY).write_text("foreign: true\n")
    if tamper == "assignment": (shared / ".local/two-site-refactor/assignments/WA-NEXT-STAGE.md").write_text("forged")
    if tamper in {"review", "same-session"}:
        p = directory / "reviewer/stream.jsonl"
        if tamper == "review": p.write_bytes(p.read_bytes() + b"{}\n")
        else:
            p.write_bytes(p.read_bytes().replace(b"synthetic-next-reviewer", b"synthetic-next-planner"))
            q = directory / "reviewer/capture.json"; v = a.decode(q.read_bytes()); v["stream_sha256"] = a.digest(p.read_bytes()); q.write_bytes(a.encode(v))
    if tamper == "cpp": monkeypatch.setattr(a, "checkpoint", lambda r: (cp, "f" * 64))
    if tamper == "stop": (root / "STOP").write_text("operator stop")
    with pytest.raises((a.AuthorityLifecycleError, driver.QoderError)):
        a.resume_pending_next_stage(root, cp["repository"]["assignment_scope"]["sha256"])
    assert git(root, "rev-parse", "HEAD").decode().strip() == context["source_head"]


def test_preexpiry_reviewed_renewal_recovers_after_old_expiry_without_authorizing_old_scope(job, monkeypatch):
    import subprocess
    root, shared, directory, plan, cp, repo = job
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(root)}
    def git(*args): return subprocess.check_output(["git", "-C", str(root), *args], env=env)
    (root / ".gitignore").write_text("/.local/\n/fake-qoder-pin\n")
    order = root / a.ORDER_PATH; order.parent.mkdir(parents=True)
    order.write_text("design_status: APPROVED\nstages:\n  P1-00b: {state: IN_PROGRESS}\n")
    git("init", "-qb", ap.BRANCH); git("config", "user.name", "Synthetic"); git("config", "user.email", "fixture@example.invalid")
    git("add", "."); git("commit", "-qm", "synthetic renewal Source")
    created = datetime.now(timezone.utc) - timedelta(hours=2)
    policy, old, reg, expiry = controls(created)
    registry = yaml.safe_load(reg); registry["integration"]["path"] = str(root); reg = yaml.safe_dump(registry, sort_keys=False).encode()
    new, after, previous, current = a.renewal_documents(old, reg, old_id=OLD, new_id=NEW,
        new_expiry=expiry, reason="test renewal", now=created, policy=policy)
    before = {"head": git("rev-parse", "HEAD").decode().strip(), "status": "", "protected_refs": ap.ref_snapshot(root),
        "control_blobs": {a.ORDER_PATH: git("rev-parse", "HEAD:" + a.ORDER_PATH).decode().strip()},
        "assignment_scope": ap.assignment_scope_context(previous)}
    cp["repository"] = copy.deepcopy(before)
    plan.update(repository=before, created_at=created.isoformat(), new_expiry=expiry, old_assignment=old.decode(),
        new_assignment=new.decode(), old_registry=reg.decode(), new_registry=after.decode(), new_scope=ap.assignment_scope_context(current))
    (shared / plan["old_assignment_path"]).write_bytes(old); (shared / a.REGISTRY).write_bytes(reg)
    new_directory = directory.with_name(previous.sha256); directory.rename(new_directory); directory = new_directory
    (directory / "intent.json").write_bytes(a.encode(plan))
    for name in ("planner", "reviewer"):
        (directory / name / "plan.json").write_bytes(a.encode(plan))
        stream = directory / name / "stream.jsonl"; events = [json.loads(line) for line in stream.read_text().splitlines()]
        value = json.loads(events[-1]["result"]); value["proposal_sha256"] = a.digest(a.encode(plan)); value["source_head"] = before["head"]
        events[-1]["result"] = json.dumps(value); stream.write_text("".join(json.dumps(e) + "\n" for e in events))
        prompt = a._review_prompt(plan).encode(); (directory / name / "prompt.txt").write_bytes(prompt)
        path = directory / name / "capture.json"; capture = a.decode(path.read_bytes()); capture.update(
            proposal_sha256=a.digest(a.encode(plan)), source_head=before["head"], reviewed_at=(created + timedelta(seconds=1)).isoformat(),
            stream_sha256=a.digest(stream.read_bytes()), prompt_sha256=a.digest(prompt)); path.write_bytes(a.encode(capture))
    monkeypatch.setattr(a, "_renewal_observed_repository", REAL_RENEWAL_OBSERVER)
    monkeypatch.setattr(a, "repository", lambda r: REAL_RENEWAL_OBSERVER(r, plan))
    monkeypatch.setattr(ap, "validate_runtime_contract", lambda _: None)  # public minimal Git fixture, runtime tested separately
    monkeypatch.setattr(ap, "CONTROL_PATHS", (Path(a.ORDER_PATH),))
    monkeypatch.setattr(scopes, "load_active_assignment_scope", REAL_ACTIVE_LOADER)
    monkeypatch.setattr(scopes, "_resolve_shared_checkout", lambda _: str(shared))
    with pytest.raises(scopes.AssignmentScopeError, match="expired"):
        scopes.load_active_assignment_scope(root)
    result = a.complete_renewal_locked(root, directory)
    assert result["new_scope"]["assignment_id"] == NEW and scopes.load_active_assignment_scope(root).assignment_id == NEW
    assert cp["repository"]["assignment_scope"]["assignment_id"] == OLD


@pytest.fixture
def pre_closeout(closed_stage, monkeypatch):
    root, shared, context, git = closed_stage
    git(root, "reset", "--hard", context["accepted_stage_head"])
    bundle = root / context["closeout_bundle"]
    (bundle / "CODEX_FINAL_REVIEW.md").write_text("# synthetic actual Stage approval audit view\n")
    cp = a.checkpoint(root)[0]
    monkeypatch.setattr(a, "repository", lambda _: copy.deepcopy(cp["repository"]))
    plan = a.prepare_closeout_plan(root); directory, pre = a.persist_closeout_plan(root, plan)
    for path, content in plan["outputs"].items():
        target = root / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content)
    git(root, "add", "--", *sorted(plan["outputs"]))
    return root, shared, pre, plan, git


def test_pre_closeout_profile_uses_actual_final_proof_and_exact_four_staged_files(pre_closeout):
    root, shared, context, plan, _ = pre_closeout
    with pytest.raises(scopes.AssignmentScopeError, match="IN_PROGRESS"):
        scopes.load_active_assignment_scope(root)
    assert a.validate_authority_review_context(root, context) == (shared / a.REGISTRY,
        shared / ".local/two-site-refactor/assignments" / (OLD + ".md"))
    assert set(plan["outputs"]) == {a.ORDER_PATH, "docs/refactor/two-server/CHANGE_LEDGER.md",
        "docs/refactor/two-server/reviews/P1-00b-" + OLD + ".json", "docs/refactor/two-server/reviews/P1-00b-" + OLD + ".md"}


@pytest.mark.parametrize("tamper", ["context", "stage-final", "working-patch", "index-extra", "registry", "plan"])
def test_pre_closeout_profile_rejects_unverified_or_foreign_controls(pre_closeout, tamper):
    root, shared, context, plan, git = pre_closeout
    if tamper == "context": context["plan_sha256"] = "f" * 64
    if tamper == "stage-final":
        p = root / plan["bundle"] / "CODEX_FINAL_REVIEW.execution.json"; v = a.decode(p.read_bytes()); v["normal_exit_observed"] = False; p.write_bytes(a.encode(v))
    if tamper == "working-patch": (root / a.ORDER_PATH).write_text(plan["outputs"][a.ORDER_PATH] + "# foreign\n")
    if tamper == "index-extra":
        p = root / "docs/refactor/two-server/MASTER_PLAN.md"; p.write_text(p.read_text() + "\nforeign\n"); git(root, "add", str(p))
    if tamper == "registry": (shared / a.REGISTRY).write_text("foreign: true\n")
    if tamper == "plan":
        p = root / context["plan_path"]; v = a.decode(p.read_bytes()); v["outputs"][a.ORDER_PATH] += "# foreign\n"; p.write_bytes(a.encode(v))
    with pytest.raises(a.AuthorityLifecycleError): a.validate_authority_review_context(root, context)


def test_bridge_explicit_precloseout_context_cannot_be_skipped_by_containment_marker(tmp_path, monkeypatch):
    import importlib.util
    from scripts import two_site_review_bridge as bridge, two_site_coordinator_sandbox as sandbox
    source = Path(__file__).with_name("test_two_site_review_bridge.py")
    spec = importlib.util.spec_from_file_location("authority_bridge_fixture", source)
    fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
    root, schema, output = fixture.setup.__wrapped__(tmp_path, monkeypatch)
    context = {"kind": "SYNTHETIC_FORWARDING_TEST_ONLY"}; observed = []
    def wrap(actual_root, directory, argv, *, environment=None, authority_review_context=None):
        observed.append((actual_root, authority_review_context, environment.get("TWO_SITE_COORDINATOR_SANDBOX")))
        return argv  # synthetic local CLI only; real profile validation is tested above
    monkeypatch.setattr(sandbox, "buildwrap_coordinator_command", wrap)
    proof = bridge.review(root, schema, output, "SYNTHETIC", authority_review_context=context)
    assert observed == [(root, context, "1")] and proof["provider"] == "qoder"


@pytest.fixture
def two_chunk_stage(closed_stage, monkeypatch):
    root, shared, context, git = closed_stage
    base = context["accepted_stage_head"]; git(root, "reset", "--hard", base)
    path = root / "tests/test_synthetic_stage.py"; path.parent.mkdir(); path.write_text("VALUE = '" + "x" * 1000 + "'\n")
    git(root, "add", "."); git(root, "commit", "-qm", "synthetic real Stage behavior")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    cp = a.checkpoint(root)[0]; cp["repository"]["head"] = head
    monkeypatch.setattr(a, "checkpoint", lambda _: (copy.deepcopy(cp), "c" * 64))
    monkeypatch.setattr(a, "repository", lambda _: copy.deepcopy(cp["repository"]))
    monkeypatch.setattr(a, "_stage_base", lambda *_: base)
    model_config = ai.load_ai_config(root); settings = model_config["roles"]["coordinator"]
    binary = root / ".local/synthetic-qoder-stage-pin"; binary.write_bytes(b"SYNTHETIC_NEVER_EXECUTED"); binary.chmod(0o400)
    runtime = {"path": str(binary), "version": "synthetic", "sha256": a.digest(binary.read_bytes())}
    monkeypatch.setattr(ai, "load_runtime_config", lambda _: {"coordinator": runtime})
    run_id = "cycle-0001-20261104T120000Z-12345"; run = root / a.STATE / "runs" / run_id; run.mkdir(mode=0o700, parents=True)
    value = {"schema_version": "1.0", "state": "BLOCKED", "disposition": "HALT", "category": "EXTERNAL_GATE",
        "summary": "Full Stage candidate awaits independent final review", "commit": None, "next_action": "Stage final review",
        "blockers": ["Independent full Stage final approval required"], "gemini_worker_run_id": None,
        "gemini_reviewer_run_id": None, "proposal_sha256": None}
    events = [{"type": "system", "subtype": "init", "session_id": "synthetic-stage-request", "model": settings["model"],
        "tools": ["Read"], "mcp_servers": [], "plugins": []}, {"type": "result", "subtype": "success", "is_error": False,
        "session_id": "synthetic-stage-request", "num_turns": 1, "stop_reason": "end_turn", "permission_denials": [], "result": json.dumps(value)}]
    a.write_new(run / "stream.jsonl", "".join(json.dumps(e) + "\n" for e in events).encode()); a.write_new(run / "result.json", a.encode(value))
    a.write_new(run / "status.json", a.encode({"state": "BLOCKED", "head_before": head, "head_after": head,
        "assignment_scope": {"sha256": cp["repository"]["assignment_scope"]["sha256"]}, "model_config": model_config,
        "coordinator": {**runtime, "provider": "qoder", "logged_in": True, "auto_update_enabled": False,
            "model": settings["model"], "reasoning": settings["reasoning_effort"]}}))
    request = {"schema_version": 1, "kind": "QODER_STAGE_FINAL_REQUEST_V1", "source_head": head,
        "assignment_scope_sha256": cp["repository"]["assignment_scope"]["sha256"], "controller_run_id": run_id,
        "base_sha": base, "stage_report": "Synthetic full Stage evidence, not real production acceptance",
        "final_review_request": "Review exact interval " + base + ".." + head, "handoff": "Synthetic fixture",
        "created_at": datetime.now(timezone.utc).isoformat()}
    diff = a._stage_interval(root, base, head); monkeypatch.setattr(a, "STAGE_CHUNK_BYTES", (len(diff) + 1) // 2)
    directory = a.prepare_stage_coverage(root, request)
    return root, shared, directory, request, cp, runtime


def _synthetic_chunk_approval(root, directory, runtime, index):
    manifest = a.decode(a.read(directory / "manifest.json", 4_000_000)); item = manifest["chunks"][index]
    plan = a._chunk_plan(manifest["template"], index, item["start_byte"], a.read(directory / item["path"]), a.digest(a.encode(manifest)))
    run = directory / ("review-" + str(index).zfill(4)); run.mkdir(mode=0o700)
    settings = ai.role_settings(root, "final_reviewer"); session = "synthetic-exact-chunk-" + str(index)
    value = {"decision": "APPROVE", "findings": [], "proposal_sha256": a.digest(a.encode(plan)), "source_head": plan["repository"]["head"]}
    events = [{"type": "system", "subtype": "init", "session_id": session, "model": settings["model"], "tools": ["mcp_list"],
        "mcp_servers": [], "plugins": []}, {"type": "result", "subtype": "success", "is_error": False, "session_id": session,
        "num_turns": 1, "stop_reason": "end_turn", "permission_denials": [], "result": json.dumps(value)}]
    stream = "".join(json.dumps(e) + "\n" for e in events).encode(); prompt = a._review_prompt(plan).encode()
    a.write_new(run / "plan.json", a.encode(plan)); a.write_new(run / "stream.jsonl", stream); a.write_new(run / "prompt.txt", prompt)
    a.write_new(run / "capture.json", a.encode({"normal_exit": True, "exit_code": 0, "role": "final_reviewer", "settings": settings,
        "proposal_sha256": a.digest(a.encode(plan)), "source_head": plan["repository"]["head"], "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "stream_sha256": a.digest(stream), "prompt_sha256": a.digest(prompt), "cli": {**runtime, "provider": "qoder", "logged_in": True,
        "auto_update_enabled": False, "model": settings["model"], "reasoning": settings["reasoning_effort"]}}))


def test_two_exact_diff_chunks_need_distinct_genuine_sdk_approvals_and_never_equal_stage_acceptance(two_chunk_stage):
    root, shared, directory, request, cp, runtime = two_chunk_stage
    manifest = a.decode(a.read(directory / "manifest.json")); assert manifest["chunk_count"] == 2
    assert a.validate_stage_coverage(root, directory, require_reviews=False)["state"] == "EXACT_DIFF_COVERAGE_PREPARED"
    with pytest.raises(a.AuthorityLifecycleError): a.validate_stage_coverage(root, directory)
    for index in range(2): _synthetic_chunk_approval(root, directory, runtime, index)
    result = a.validate_stage_coverage(root, directory)
    assert result["state"] == "ALL_EXACT_DIFF_CHUNKS_APPROVED" and len(set(result["review_sessions"])) == 2
    assert result["whole_stage_acceptance"] is False and cp["repository"]["head"] == request["source_head"]
    assert "IN_PROGRESS" in (root / a.ORDER_PATH).read_text()


@pytest.mark.parametrize("tamper", ["gap", "overlap", "omitted", "chunk-bytes", "source", "plan", "stream", "same-session"])
def test_exact_stage_review_coverage_rejects_missing_forged_or_stale_proof(two_chunk_stage, tamper):
    root, shared, directory, request, cp, runtime = two_chunk_stage
    for index in range(2): _synthetic_chunk_approval(root, directory, runtime, index)
    p = directory / "manifest.json"; manifest = a.decode(p.read_bytes())
    if tamper in {"gap", "overlap"}:
        manifest["chunks"][1]["start_byte"] += 1 if tamper == "gap" else -1; p.write_bytes(a.encode(manifest))
    if tamper == "omitted": manifest["chunks"].pop(); p.write_bytes(a.encode(manifest))
    if tamper == "chunk-bytes": (directory / "chunks/0001.diff").write_bytes(b"summary substitute")
    if tamper == "source": cp["repository"]["head"] = "f" * 40
    if tamper == "plan":
        p = directory / "review-0001/plan.json"; v = a.decode(p.read_bytes()); v["diff_chunk"] = "summary substitute"; p.write_bytes(a.encode(v))
    if tamper == "stream":
        p = directory / "review-0001/stream.jsonl"; p.write_bytes(p.read_bytes() + b"{}\n")
    if tamper == "same-session":
        p = directory / "review-0001/stream.jsonl"; p.write_bytes(p.read_bytes().replace(b"synthetic-exact-chunk-1", b"synthetic-exact-chunk-0"))
        q = directory / "review-0001/capture.json"; v = a.decode(q.read_bytes()); v["stream_sha256"] = a.digest(p.read_bytes()); q.write_bytes(a.encode(v))
    with pytest.raises((a.AuthorityLifecycleError, driver.QoderError)):
        a.validate_stage_coverage(root, directory)


def test_stage_native_matrix_is_source_bound_complete_and_still_not_stage_acceptance(two_chunk_stage, monkeypatch):
    from scripts import two_site_agent_sandbox as sandbox
    root, shared, directory, request, cp, runtime = two_chunk_stage
    for i in range(2): _synthetic_chunk_approval(root, directory, runtime, i)
    seen = []
    def native(actual, selectors, **kwargs):
        seen.append((actual, selectors, kwargs))
        return {"returncode": 0, "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all", "snapshot_files": 10,
            "snapshot_bytes": 100, "stdout": "1 passed", "stderr": ""}
    monkeypatch.setattr(sandbox, "run_tests", native)
    matrix = a.run_stage_native_locked(root, directory)
    assert seen[0][1] == ["tests/test_synthetic_stage.py"] and seen[0][2]["structured_diagnostics"] is True
    assert matrix["whole_stage_acceptance"] is False
    result = a.validate_stage_final_evidence(root, directory)
    assert result["state"] == "STAGE_FINAL_EVIDENCE_READY" and result["whole_stage_acceptance"] is False
    p = directory / "native-0000.json"; value = a.decode(p.read_bytes()); value["selectors"] = []
    p.write_bytes(a.encode(value))
    with pytest.raises(a.AuthorityLifecycleError, match="NATIVE_BINDINGS"): a.validate_stage_final_evidence(root, directory)


def test_stage_native_failure_is_preserved_before_any_review(two_chunk_stage, monkeypatch):
    from scripts import two_site_agent_sandbox as sandbox
    root, shared, directory, request, cp, runtime = two_chunk_stage
    monkeypatch.setattr(sandbox, "run_tests", lambda *args, **kwargs: {"returncode": 1,
        "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all", "snapshot_files": 10,
        "snapshot_bytes": 100, "stdout": "actual synthetic assertion failed", "stderr": ""})
    with pytest.raises(a.AuthorityLifecycleError, match="NATIVE_NOT_PASSED"): a.run_stage_native_locked(root, directory)
    assert a.decode(a.read(directory / "native-0000.json"))["result"]["returncode"] == 1
    assert not (directory / "review-0000").exists()
    result = a._quality_rejection(root, directory, "AUTHORITY_STAGE_NATIVE_NOT_PASSED", [directory / "native-0000.json"])
    assert result["resume_ordinary"] is True and result["repository"] == cp["repository"] and result["business_acceptance_credit"] == 0
    hint = a.lifecycle_feedback(root, cp["repository"])
    assert hint["source_head"] == request["source_head"] and hint["records"][0]["reason_code"] == "AUTHORITY_STAGE_NATIVE_NOT_PASSED"


def _synthetic_closeout_review(root, plan):
    bundle = root / plan["bundle"]; schema_name = "closeout-review.schema.json"
    schema = json.loads((root / "docs/refactor/two-server/templates" / schema_name).read_text())
    receipt = _schema_value(schema); before = plan["repository"]
    patch = a._git_raw(root, "diff", "--cached", "--binary", "--full-index", "--no-ext-diff", before["head"])
    receipt.update(stage_id=before["assignment_scope"]["stage_id"], assignment_id=before["assignment_scope"]["assignment_id"],
        accepted_stage_sha=before["head"], proposed_patch_sha256=a.digest(patch), reviewed_at_utc=datetime.now(timezone.utc).isoformat(),
        verdict="APPROVE", findings=[])
    receipt["scope"].update(unauthorized_external_mutation="NONE"); receipt["decision"].update(commit_authorized=True, conditions=[])
    target = bundle / "CODEX_CLOSEOUT_PREACTION.json"; raw = (json.dumps(receipt, sort_keys=True) + "\n").encode(); target.write_bytes(raw)
    role_raw = (root / ai.AI_CONFIG_REL).read_bytes(); role = json.loads(role_raw)["roles"]["final_reviewer"]
    relative = Path(".local/two-site-refactor/review-bridge/runs") / ("c" * 32); run = root / relative; run.mkdir(parents=True, exist_ok=True)
    session = "synthetic-producer-closeout"; stream = (json.dumps({"type": "system", "subtype": "init", "session_id": session,
        "model": role["model"], "tools": ["mcp_list"], "mcp_servers": [], "plugins": []}) + "\n" + json.dumps({"type": "result",
        "subtype": "success", "is_error": False, "session_id": session, "num_turns": 1, "stop_reason": "end_turn", "permission_denials": [],
        "result": json.dumps(receipt)}) + "\n").encode()
    prompt = b"SYNTHETIC COMPLETE CLOSEOUT REVIEW PROMPT"
    (run / "prompt.txt").write_bytes(prompt); (run / "stream.jsonl").write_bytes(stream); (run / "result.json").write_bytes(raw)
    execution = {"schema": "qoder-schema-review-execution/1", "provider": "qoder",
        "role": "final_reviewer", "normal_exit_observed": True, "exit_code": 0,
        "run_dir": relative.as_posix(), "model_config": role, "session_id": session, "stream_sha256": a.digest(stream),
        "prompt_sha256": a.digest(prompt),
        "result_sha256": a.digest(raw), "review_schema_sha256": a.digest((root / "docs/refactor/two-server/templates" / schema_name).read_bytes()),
        "model_policy_sha256": a.digest(role_raw)}
    (run / "execution.json").write_bytes(a.encode(execution))
    target.with_suffix(".execution.json").write_bytes(a.encode({**execution,
        "published_receipt_sha256": a.digest(raw)}))


def test_real_closeout_producer_commit_verifies_and_keeps_old_cpp(pre_closeout, monkeypatch):
    root, shared, context, plan, git = pre_closeout
    directory = (root / context["plan_path"]).parent
    for p in (root / plan["bundle"] / "CODEX_CLOSEOUT_PREACTION.json", root / plan["bundle"] / "CODEX_CLOSEOUT_PREACTION.execution.json"):
        p.unlink(missing_ok=True)  # discard unrelated prior synthetic closeout proof
    actual_wrapper = a._stage_wrapper; seen = []
    def wrapper(actual, operation, **kwargs):
        seen.append(operation)
        if operation == "closeout-review":
            a.validate_pre_closeout_context(root, a.decode(a.read(kwargs["context"])))
            _synthetic_closeout_review(root, plan); return {"operation": operation, "exit_code": 0}
        return actual_wrapper(actual, operation, **kwargs)  # real existing --verify-commit; no model path
    monkeypatch.setattr(a, "_stage_wrapper", wrapper)
    before_cp = copy.deepcopy(a.checkpoint(root))
    result = a.complete_closeout_locked(root, directory)
    assert seen == ["closeout-review", "closeout-verify"]
    assert result["state"] == "STAGE_CLOSEOUT_VERIFIED" and result["whole_plan_complete"] is False
    assert git(root, "rev-list", "--parents", "-n", "1", result["source_head"]).decode().split() == [result["source_head"], plan["repository"]["head"]]
    assert a.checkpoint(root) == before_cp
    assert a.validate_authority_review_context(root, result["authority_review_context"])[0] == shared / a.REGISTRY
    assert a.complete_closeout_locked(root, directory) == result
    # Crash after the genuine commit but before durable postcommit observation:
    # after TTL, replay exact Git commit time/proofs, never invent an old CLI0.
    (directory / "post-commit-verified.json").unlink()
    observed_later = datetime.now(timezone.utc) + timedelta(hours=2)
    class Later(datetime):
        @classmethod
        def now(cls, tz=None): return observed_later if tz is None else observed_later.astimezone(tz)
    monkeypatch.setattr(a, "datetime", Later)
    later = a.complete_closeout_locked(root, directory)
    historical = a.decode(a.read(directory / "post-commit-verified.json"))["observation"]
    assert historical["operation"] == "closeout-verify-historical" and historical["cli_called"] is False
    assert "exit_code" not in historical and later["source_head"] == result["source_head"]
    assert a.validate_authority_review_context(root, later["authority_review_context"])[0] == shared / a.REGISTRY
    assert a.checkpoint(root) == before_cp


def test_closeout_producer_partial_write_resume_and_foreign_source_rejection(pre_closeout, monkeypatch):
    root, shared, context, plan, git = pre_closeout
    directory = (root / context["plan_path"]).parent
    # Real exact old Source with one already materialized planned output models a crash.
    git(root, "reset", "--hard", plan["repository"]["head"])
    for relative in plan["outputs"]:
        if not a._git_blob_optional(root, plan["repository"]["head"], relative): (root / relative).unlink(missing_ok=True)
    first = next(iter(plan["outputs"])); (root / first).write_text(plan["outputs"][first])
    assert a._closeout_state(root, directory)[1] == plan["repository"]["head"]
    a._apply_closeout_outputs(root, directory, plan)
    a.validate_pre_closeout_context(root, context)
    (root / a.ORDER_PATH).write_text(plan["outputs"][a.ORDER_PATH] + "# foreign\n")
    with pytest.raises(a.AuthorityLifecycleError, match="CLOSEOUT_BYTES"):
        a.complete_closeout_locked(root, directory)


def test_scope_proposal_is_actual_configured_coordinator_sdk_and_bound_to_protected_contract(closed_stage, monkeypatch):
    root, shared, context, git = closed_stage
    eligible = a._eligible_next_stages(root, context); assert eligible == ["P1-01"]
    expires = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    policy = a.load_policy(root)[0]
    header = yaml.safe_load(document(expires).decode().split("```yaml\n")[1].split("```", 1)[0])
    header.update(id="WA-P1-01-AUTO-" + context["source_head"][:12], stage_id="P1-01", parent_assignment_id=OLD,
        forbidden_paths=policy["forbidden_scope_patterns"])
    value = {"source_head": context["source_head"], "next_stage": "P1-01",
        "assignment_markdown": "# next Stage\n```yaml\n" + yaml.safe_dump(header, sort_keys=False) + "```\n"}
    settings = ai.role_settings(root, "coordinator"); directory = root / ".local/synthetic-scope-proposal"; directory.mkdir(mode=0o700)
    binary = directory / "fake-pin"; binary.write_bytes(b"SYNTHETIC_NEVER_EXECUTED"); binary.chmod(0o400)
    runtime = {"path": str(binary), "version": "synthetic", "sha256": a.digest(binary.read_bytes())}
    monkeypatch.setattr(ai, "load_runtime_config", lambda _: {"coordinator": runtime})
    prompt = a._scope_proposal_prompt(root, context, eligible, expires).encode(); assert len(prompt) < a.LIMIT
    events = [{"type": "system", "subtype": "init", "session_id": "synthetic-coordinator-scope-author", "model": settings["model"],
        "tools": ["mcp_list"], "mcp_servers": [], "plugins": []}, {"type": "result", "subtype": "success", "is_error": False,
        "session_id": "synthetic-coordinator-scope-author", "num_turns": 1, "stop_reason": "end_turn", "permission_denials": [], "result": json.dumps(value)}]
    stream = "".join(json.dumps(e) + "\n" for e in events).encode(); a.write_new(directory / "stream.jsonl", stream); a.write_new(directory / "prompt.txt", prompt)
    capture = {"kind": "QODER_NEXT_SCOPE_PROPOSAL_V1", "source_head": context["source_head"], "expires_at": expires,
        "settings": settings, "normal_exit_observed": True, "exit_code": 0, "prompt_sha256": a.digest(prompt), "stream_sha256": a.digest(stream),
        "cli": {**runtime, "provider": "qoder", "logged_in": True, "auto_update_enabled": False, "model": settings["model"], "reasoning": settings["reasoning_effort"]}}
    a.write_new(directory / "capture.json", a.encode(capture))
    assert a._validate_scope_proposal(root, directory, context, eligible) == (value, "synthetic-coordinator-scope-author")
    events[-1]["result"] = json.dumps({**value, "source_head": "f" * 40})
    changed = "".join(json.dumps(e) + "\n" for e in events).encode(); (directory / "stream.jsonl").write_bytes(changed)
    capture["stream_sha256"] = a.digest(changed); (directory / "capture.json").write_bytes(a.encode(capture))
    with pytest.raises(a.AuthorityLifecycleError, match="PROPOSAL_CONTRACT"):
        a._validate_scope_proposal(root, directory, context, eligible)


def test_lifecycle_dispatcher_reclassifies_owns_holds_and_never_treats_no_action_as_work(tmp_path, monkeypatch):
    from contextlib import contextmanager
    root = tmp_path; calls = []
    @contextmanager
    def holds(actual):
        calls.append("MON_AP_HELD"); yield; calls.append("RELEASED")
    monkeypatch.setattr(a, "authority_holds", holds)
    def classify(actual):
        assert calls == ["MON_AP_HELD"]; calls.append("CLASSIFIED"); return None
    monkeypatch.setattr(a, "classify_lifecycle_action", classify)
    assert a.dispatch_lifecycle(root) == {"state": "NO_ACTION"}
    assert calls == ["MON_AP_HELD", "CLASSIFIED", "RELEASED"]


def test_lifecycle_classifier_yields_to_other_reviewed_source_transition(tmp_path, monkeypatch):
    old = {"sha256": "a" * 64, "expires_at": "2099-01-01T00:00:00+00:00"}
    checkpoint_repository = {"head": "b" * 40, "assignment_scope": old}
    current_repository = {"head": "c" * 40, "assignment_scope": old}
    monkeypatch.setattr(a, "load_policy", lambda root: ({"renewal_window_seconds": 86400}, "d" * 64))
    monkeypatch.setattr(a, "checkpoint", lambda root: ({"repository": checkpoint_repository}, "e" * 64))
    monkeypatch.setattr(a, "repository", lambda root: current_repository)
    assert a.classify_lifecycle_action(tmp_path) is None


def test_monitor_lock_spawn_race_waits_before_ap_without_lock_inversion(tmp_path, monkeypatch):
    import fcntl
    import threading
    import time
    from contextlib import contextmanager, nullcontext
    from scripts import two_site_provider_broker as broker, two_site_proposal_pipeline as pipeline, two_site_proposal_runner as runner
    root = tmp_path; path = root / ".local/two-site-refactor/monitor/monitor.lock"; path.parent.mkdir(parents=True)
    for name in ("requests", "claimed"): (root / broker.STATE_REL / name).mkdir(parents=True)
    descriptor = path.open("wb"); fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX)
    order = []; entered = threading.Event()
    @contextmanager
    def ap_hold(_):
        assert not descriptor.closed; order.append("AP"); yield
    monkeypatch.setattr(ap, "exclusive_supervisor_lock", ap_hold)
    monkeypatch.setattr(broker, "exclusive_broker_lock", lambda _: nullcontext())
    monkeypatch.setattr(pipeline, "pipeline_lock", lambda _: nullcontext())
    monkeypatch.setattr(runner, "acquire_proposal_lock", lambda _: (42, None))
    monkeypatch.setattr(runner, "release_proposal_lock", lambda _: None)
    def child():
        with a.authority_holds(root): entered.set()
    worker = threading.Thread(target=child); worker.start(); time.sleep(0.08)
    assert not entered.is_set() and not order  # AP cannot precede parent MON release
    fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN); worker.join(timeout=3); descriptor.close()
    assert entered.is_set() and order == ["AP"] and not worker.is_alive()
