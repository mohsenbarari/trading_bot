from __future__ import annotations

import os
os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

import json
from pathlib import Path
import stat
import pytest

from scripts.two_site_assignment_scope import AssignmentScope
from scripts import two_site_codex_autopilot as ap
from scripts import two_site_proposal_runner as runner
from scripts.two_site_preparation_boundary import validate_resolved_preparation_boundary
from scripts.two_site_preparation_contract import PreparationStartup
from tests.test_two_site_cycle_packet import repo, make_proposal_chain, UPDATED_CODE
from tests.two_site_generation_fixture import install_current_contract, seal_completion_fixture


def _setup_valid_boundary(repo, monkeypatch):
    root, git = repo
    # This is an offline specimen, not evidence that a Provider was invoked.
    # Capture the current contract in the actual owned source-head tree FIRST.
    install_current_contract(root)
    git("add", "--", str(runner.GEMINI_RUNTIME_CONTRACT_REL),
        *(str(path) for path in runner.GEMINI_RUNTIME_REFERENCE_PATHS))
    git("commit", "-m", "current offline generation contract")
    output, digest, result, reviewer_dir, before_head = make_proposal_chain(repo)
    worker_dir = root / ".local/two-site-refactor/proposals/w1"

    for path in (worker_dir / "task.json", reviewer_dir / "task.json", output):
        data = json.loads(path.read_text())
        if "task" in data:
            data["task"]["goal"] = data["task"]["goal"] + " WA-TEST-ROLLBACK-155"
        elif "goal" in data:
            data["goal"] = data["goal"] + " WA-TEST-ROLLBACK-155"
        path.write_text(json.dumps(data))

    # Seal AFTER legitimate task edits; both distinct sessions traverse the real
    # mandatory completion reader. These synthetic artifacts grant no execution.
    seal_completion_fixture(root, worker_dir)
    seal_completion_fixture(root, reviewer_dir)

    for change in result["changes"]:
        fp = root / change["path"]
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(change["content"])
        git("add", "--", change["path"])
    git("commit", "-m", "proposal commit")
    after_head = git("rev-parse", "HEAD")

    scope = AssignmentScope(
        assignment_id="WA-TEST-ROLLBACK-155",
        stage_id="P1-00b",
        allowed_paths=("code.py", "test_new.py", "doc.md"),
        forbidden_paths=(),
        expires_at="2099-01-01T00:00:00Z",
        sha256="d" * 64,
    )
    facts = {
        "head": after_head,
        "status": "",
        "protected_refs": ap.ref_snapshot(root),
        "control_blobs": {"gap.md": "a" * 40},
        "assignment_scope": scope,
    }
    monkeypatch.setattr(ap, "validate_repository", lambda r, require_clean=True: dict(facts))

    state_root = root / ".local/two-site-refactor/autopilot"
    runs_dir = state_root / "runs"
    cycle_id = "cycle-0001-20260922T120000Z-1"
    run_dir = runs_dir / cycle_id
    run_dir.mkdir(parents=True, exist_ok=True)

    startup = PreparationStartup(
        schema_version=1,
        route="one_shot",
        cycle_id=cycle_id,
        source_head=before_head,
        assignment_sha256=scope.sha256,
    )
    startup_bytes = json.dumps(
        startup.to_mapping(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    (run_dir / "preparation-startup.json").write_bytes(startup_bytes)

    raw_result = {
        "schema_version": "1.0",
        "state": "CONTINUE",
        "disposition": "NONE",
        "category": "NONE",
        "summary": "Completed bounded supervisor startup cycle successfully",
        "commit": after_head,
        "next_action": "Execute subsequent verified refactor cycle",
        "blockers": [],
        "gemini_worker_run_id": "w1",
        "gemini_reviewer_run_id": "r1",
        "proposal_sha256": digest,
    }
    (run_dir / "result.json").write_text(json.dumps(raw_result, indent=2))

    meta = {
        "state": "CONTINUE",
        "blockers": [],
        "head_before": before_head,
        "head_after": after_head,
        "protected_refs": facts["protected_refs"],
        "assignment_scope": {
            "assignment_id": scope.assignment_id,
            "stage_id": scope.stage_id,
            "sha256": scope.sha256,
        },
        "gemini_worker_run_id": "w1",
        "gemini_reviewer_run_id": "r1",
        "proposal_sha256": digest,
        "preparation_startup": startup.to_mapping(),
        "preparation_startup_sha256": startup.fingerprint,
    }
    (run_dir / "status.json").write_text(json.dumps(meta, indent=2))

    globalstatus = {
        "state": "CONTINUE",
        "blockers": [],
        "head": after_head,
        "head_after": after_head,
        "commit": after_head,
        "head_changed": True,
        "gemini_worker_run_id": "w1",
        "gemini_reviewer_run_id": "r1",
        "proposal_sha256": digest,
        "evidence": str(run_dir),
    }
    (state_root / "status.json").write_text(json.dumps(globalstatus, indent=2))

    # Every fault below starts from the same complete, accepted offline control.
    assert validate_resolved_preparation_boundary(root, state_root) is None
    for relative in (runner.GEMINI_RUNTIME_CONTRACT_REL, *runner.GEMINI_RUNTIME_REFERENCE_PATHS):
        assert ap.git_blob(root, before_head, str(relative)) == (root / relative).read_bytes()

    return {
        "root": root,
        "state_root": state_root,
        "run_dir": run_dir,
        "git": git,
        "facts": facts,
        "before_head": before_head,
        "after_head": after_head,
        "digest": digest,
        "scope": scope,
        "startup": startup,
        "worker_dir": worker_dir,
        "reviewer_dir": reviewer_dir,
        "raw_result": raw_result,
        "meta": meta,
        "globalstatus": globalstatus,
    }


def test_accepts_real_resolved_chain_without_mutation(repo, monkeypatch):
    ctx = _setup_valid_boundary(repo, monkeypatch)
    root, state_root, git = ctx["root"], ctx["state_root"], ctx["git"]

    head_before = git("rev-parse", "HEAD")
    status_before = git("status", "--porcelain=v1")
    refs_before = ap.ref_snapshot(root)
    bytes_before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    assert validate_resolved_preparation_boundary(root, state_root) is None

    assert git("rev-parse", "HEAD") == head_before
    assert git("status", "--porcelain=v1") == status_before
    assert ap.ref_snapshot(root) == refs_before
    bytes_after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert bytes_before == bytes_after


@pytest.mark.parametrize("mutation", [
    "global_BLOCKED", "global_RUNNING", "global_RESCOPE", "global_RETRYABLE",
    "STOP", "missingevidence", "alias_only_evidence",
    "alias_only_scope_keys", "alias_only_startup_digest",
    "wrongpath", "parentalias",
    "meta_HEAD_mismatch", "meta_scope_mismatch", "meta_ref_mismatch",
    "mismatched_worker", "mismatched_proposal_SHA",
    "changed_startup_whitespace", "duplicate_JSON", "size_limit",
    "symlink", "hardlink", "FIFO", "missing_runtime_contract", "missing_completion_proof",
])
def test_rejects_ambiguous_or_tampered_evidence(repo, monkeypatch, mutation):
    ctx = _setup_valid_boundary(repo, monkeypatch)
    root, state_root, run_dir = ctx["root"], ctx["state_root"], ctx["run_dir"]
    gs_path = state_root / "status.json"
    meta_path = run_dir / "status.json"
    res_path = run_dir / "result.json"
    sup_path = run_dir / "preparation-startup.json"

    if mutation == "global_BLOCKED":
        ctx["globalstatus"]["state"] = "BLOCKED"
        ctx["globalstatus"]["blockers"] = ["blocker"]
        gs_path.write_text(json.dumps(ctx["globalstatus"]))
    elif mutation == "global_RUNNING":
        ctx["globalstatus"]["state"] = "RUNNING"
        gs_path.write_text(json.dumps(ctx["globalstatus"]))
    elif mutation == "global_RESCOPE":
        ctx["globalstatus"]["state"] = "RESCOPE"
        gs_path.write_text(json.dumps(ctx["globalstatus"]))
    elif mutation == "global_RETRYABLE":
        ctx["globalstatus"]["state"] = "RETRYABLE"
        gs_path.write_text(json.dumps(ctx["globalstatus"]))
    elif mutation == "STOP":
        (state_root / "STOP").write_text("stop")
    elif mutation == "missingevidence":
        ctx["globalstatus"].pop("evidence", None)
        gs_path.write_text(json.dumps(ctx["globalstatus"]))
    elif mutation == "alias_only_evidence":
        ctx["globalstatus"].pop("evidence", None)
        ctx["globalstatus"]["run_dir"] = str(run_dir)
        ctx["globalstatus"]["evidence_path"] = str(run_dir)
        gs_path.write_text(json.dumps(ctx["globalstatus"]))
    elif mutation == "alias_only_scope_keys":
        ctx["meta"]["assignment_scope"] = {
            "id": ctx["scope"].assignment_id,
            "stage": ctx["scope"].stage_id,
            "sha": ctx["scope"].sha256,
        }
        meta_path.write_text(json.dumps(ctx["meta"]))
    elif mutation == "alias_only_startup_digest":
        ctx["meta"].pop("preparation_startup_sha256", None)
        ctx["meta"]["startup_sha256"] = ctx["startup"].fingerprint
        ctx["meta"]["preparation_startup_fingerprint"] = ctx["startup"].fingerprint
        meta_path.write_text(json.dumps(ctx["meta"]))
    elif mutation == "wrongpath":
        ctx["globalstatus"]["evidence"] = str(root / "other_dir")
        gs_path.write_text(json.dumps(ctx["globalstatus"]))
    elif mutation == "parentalias":
        ctx["globalstatus"]["evidence"] = str(run_dir.parent / ".." / "runs" / run_dir.name)
        gs_path.write_text(json.dumps(ctx["globalstatus"]))
    elif mutation == "meta_HEAD_mismatch":
        ctx["meta"]["head_after"] = "0" * 40
        meta_path.write_text(json.dumps(ctx["meta"]))
    elif mutation == "meta_scope_mismatch":
        ctx["meta"]["assignment_scope"]["assignment_id"] = "WRONG_SCOPE"
        meta_path.write_text(json.dumps(ctx["meta"]))
    elif mutation == "meta_ref_mismatch":
        ctx["meta"]["protected_refs"] = {"refs/heads/main": "0" * 40}
        meta_path.write_text(json.dumps(ctx["meta"]))
    elif mutation == "mismatched_worker":
        ctx["meta"]["gemini_worker_run_id"] = "w_wrong"
        meta_path.write_text(json.dumps(ctx["meta"]))
    elif mutation == "mismatched_proposal_SHA":
        ctx["globalstatus"]["proposal_sha256"] = "f" * 64
        gs_path.write_text(json.dumps(ctx["globalstatus"]))
    elif mutation == "changed_startup_whitespace":
        sup_path.write_text(sup_path.read_text() + " ")
    elif mutation == "duplicate_JSON":
        res_path.write_text('{"schema_version":"1.0","state":"CONTINUE","state":"CONTINUE"}')
    elif mutation == "size_limit":
        res_path.write_text(" " * 65000)
    elif mutation == "symlink":
        orig = meta_path.read_text()
        meta_path.unlink()
        target = run_dir / "target_status.json"
        target.write_text(orig)
        meta_path.symlink_to(target)
    elif mutation == "hardlink":
        os.link(str(meta_path), str(run_dir / "hardlink_status.json"))
    elif mutation == "FIFO":
        res_path.unlink()
        os.mkfifo(str(res_path))
    elif mutation == "missing_runtime_contract":
        (root / runner.GEMINI_RUNTIME_CONTRACT_REL).unlink()
    elif mutation == "missing_completion_proof":
        (ctx["worker_dir"] / "transport-completion.json").unlink()

    with pytest.raises(ap.AutopilotError) as exc_info:
        validate_resolved_preparation_boundary(root, state_root)
    err_msg = str(exc_info.value)
    assert str(root) not in err_msg
    assert "WA-TEST-ROLLBACK-155" not in err_msg


def test_canonical_key_ignores_irrelevant_aliases(repo, monkeypatch):
    ctx = _setup_valid_boundary(repo, monkeypatch)
    root, state_root, run_dir = ctx["root"], ctx["state_root"], ctx["run_dir"]
    ctx["globalstatus"]["run_dir"] = "/irrelevant/alias/path"
    ctx["globalstatus"]["evidence_path"] = "/another/alias"
    (state_root / "status.json").write_text(json.dumps(ctx["globalstatus"]))

    ctx["meta"]["assignment_scope"]["id"] = "IRRELEVANT_ID"
    ctx["meta"]["assignment_scope"]["stage"] = "IRRELEVANT_STAGE"
    ctx["meta"]["assignment_scope"]["sha"] = "IRRELEVANT_SHA"
    ctx["meta"]["startup_fingerprint"] = "0" * 64
    ctx["meta"]["startup_sha256"] = "0" * 64
    (run_dir / "status.json").write_text(json.dumps(ctx["meta"]))

    assert validate_resolved_preparation_boundary(root, state_root) is None


def test_real_run_loop_status_schema(repo, monkeypatch):
    ctx = _setup_valid_boundary(repo, monkeypatch)
    root, state_root, run_dir, git = ctx["root"], ctx["state_root"], ctx["run_dir"], ctx["git"]
    raw_result = ctx["raw_result"]
    before_head = ctx["before_head"]
    after_head = ctx["after_head"]
    digest = ctx["digest"]

    os.chmod(state_root, 0o700)
    for ref in ap.PROTECTED_REFS:
        git("update-ref", ref, after_head)

    ref_snap = ap.ref_snapshot(root)
    assert set(ref_snap.keys()) == set(ap.PROTECTED_REFS)
    assert all(
        isinstance(v, str) and len(v) == 40 and all(c in "0123456789abcdef" for c in v)
        for v in ref_snap.values()
    )

    ctx["facts"]["protected_refs"] = ref_snap
    ctx["facts"]["control_blobs"] = {p.as_posix(): "a" * 40 for p in ap.CONTROL_PATHS}

    ctx["meta"]["protected_refs"] = ref_snap
    (run_dir / "status.json").write_text(json.dumps(ctx["meta"], indent=2))

    fake_result = dict(
        raw_result,
        head_before=before_head,
        head_after=after_head,
        head_changed=True,
        advisories=[],
    )

    result_projection = {k: fake_result[k] for k in ap.RESULT_KEYS}
    assert ap.validate_result(result_projection) == result_projection
    assert fake_result["head_before"] == before_head
    assert fake_result["head_after"] == after_head
    assert fake_result["head_changed"] is True

    def fake_run_cycle(*args, **kwargs):
        before_model = kwargs.get("before_model")
        if before_model is not None:
            before_model(dict(ctx["facts"]))
        return run_dir, fake_result

    monkeypatch.setattr(ap, "run_cycle", fake_run_cycle)

    dummy_codex = root / "dummy_codex"
    ret = ap.run_loop(root, dummy_codex, max_cycles=1, timeout=300, once=True)
    assert ret == 2

    produced_status = json.loads((state_root / "status.json").read_text())
    assert "evidence" in produced_status
    assert produced_status["evidence"] == str(run_dir)
    assert "run_dir" not in produced_status
    assert "evidence_path" not in produced_status

    assert validate_resolved_preparation_boundary(root, state_root) is None


def test_real_review_and_commit_proofs_cannot_be_forged(repo, monkeypatch):
    ctx = _setup_valid_boundary(repo, monkeypatch)
    root, state_root, run_dir, git = ctx["root"], ctx["state_root"], ctx["run_dir"], ctx["git"]
    reviewer_dir, worker_dir = ctx["reviewer_dir"], ctx["worker_dir"]
    facts = ctx["facts"]
    original_reviewer_result = (reviewer_dir / "result.json").read_bytes()
    original_reviewer_status = (reviewer_dir / "status.json").read_bytes()

    rev_res = json.loads((reviewer_dir / "result.json").read_text())
    rev_res["status"] = "CHANGES_REQUIRED"
    rev_res["findings"] = ["reviewer requires changes"]
    (reviewer_dir / "result.json").write_text(json.dumps(rev_res))
    rev_stat = json.loads((reviewer_dir / "status.json").read_text())
    rev_stat["state"] = "CHANGES_REQUIRED"
    rev_stat["result_sha256"] = runner.file_sha256(reviewer_dir / "result.json")
    (reviewer_dir / "status.json").write_text(json.dumps(rev_stat))
    with pytest.raises(ap.AutopilotError, match="Gemini Worker/Reviewer evidence is not exactly bound"):
        validate_resolved_preparation_boundary(root, state_root)

    # Restore the original sealed bytes, never reseal a forged reviewer result.
    (reviewer_dir / "result.json").write_bytes(original_reviewer_result)
    (reviewer_dir / "status.json").write_bytes(original_reviewer_status)
    assert validate_resolved_preparation_boundary(root, state_root) is None

    rev_stat = json.loads((reviewer_dir / "status.json").read_text())
    rev_stat["conversation_id"] = "wc"
    (reviewer_dir / "status.json").write_text(json.dumps(rev_stat))
    with pytest.raises(ap.AutopilotError, match="independent fresh session"):
        validate_resolved_preparation_boundary(root, state_root)
    (reviewer_dir / "status.json").write_bytes(original_reviewer_status)
    assert validate_resolved_preparation_boundary(root, state_root) is None

    orig_worker_result = (worker_dir / "result.json").read_text()
    (worker_dir / "result.json").write_text(orig_worker_result + " ")
    with pytest.raises(ap.AutopilotError, match="Gemini Worker/Reviewer evidence is not exactly bound"):
        validate_resolved_preparation_boundary(root, state_root)
    (worker_dir / "result.json").write_text(orig_worker_result)
    assert validate_resolved_preparation_boundary(root, state_root) is None

    (root / "extra_unrelated.py").write_text("x = 1\n")
    git("add", "--", "extra_unrelated.py")
    git("commit", "--amend", "--no-edit")
    mutated_head = git("rev-parse", "HEAD")
    facts["head"] = mutated_head
    ctx["globalstatus"]["head"] = mutated_head
    ctx["globalstatus"]["head_after"] = mutated_head
    ctx["globalstatus"]["commit"] = mutated_head
    (state_root / "status.json").write_text(json.dumps(ctx["globalstatus"]))
    ctx["meta"]["head_after"] = mutated_head
    (run_dir / "status.json").write_text(json.dumps(ctx["meta"]))
    ctx["raw_result"]["commit"] = mutated_head
    (run_dir / "result.json").write_text(json.dumps(ctx["raw_result"]))
    assert ap.validate_scoped_gemini_chain(
        root, ctx["before_head"], ctx["raw_result"], ctx["scope"]
    ) == json.loads(orig_worker_result)
    with pytest.raises(ap.AutopilotError, match="assignment scope validation failed"):
        validate_resolved_preparation_boundary(root, state_root)

    git("rm", "--", "extra_unrelated.py")
    (root / "code.py").write_text("def forged():\n    return 42\n")
    git("add", "--", "code.py")
    git("commit", "--amend", "--no-edit")
    mutated_head = git("rev-parse", "HEAD")
    facts["head"] = mutated_head
    ctx["globalstatus"]["head"] = mutated_head
    ctx["globalstatus"]["head_after"] = mutated_head
    ctx["globalstatus"]["commit"] = mutated_head
    (state_root / "status.json").write_text(json.dumps(ctx["globalstatus"]))
    ctx["meta"]["head_after"] = mutated_head
    (run_dir / "status.json").write_text(json.dumps(ctx["meta"]))
    ctx["raw_result"]["commit"] = mutated_head
    (run_dir / "result.json").write_text(json.dumps(ctx["raw_result"]))
    assert ap.validate_scoped_gemini_chain(
        root, ctx["before_head"], ctx["raw_result"], ctx["scope"]
    ) == json.loads(orig_worker_result)
    with pytest.raises(ap.AutopilotError, match="committed bytes differ from the Gemini proposal"):
        validate_resolved_preparation_boundary(root, state_root)

    (root / "code.py").write_text(UPDATED_CODE)
    os.chmod(root / "code.py", stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    git("add", "--", "code.py")
    git("commit", "--amend", "--no-edit")
    mutated_head = git("rev-parse", "HEAD")
    facts["head"] = mutated_head
    ctx["globalstatus"]["head"] = mutated_head
    ctx["globalstatus"]["head_after"] = mutated_head
    ctx["globalstatus"]["commit"] = mutated_head
    (state_root / "status.json").write_text(json.dumps(ctx["globalstatus"]))
    ctx["meta"]["head_after"] = mutated_head
    (run_dir / "status.json").write_text(json.dumps(ctx["meta"]))
    ctx["raw_result"]["commit"] = mutated_head
    (run_dir / "result.json").write_text(json.dumps(ctx["raw_result"]))
    assert ap.validate_scoped_gemini_chain(
        root, ctx["before_head"], ctx["raw_result"], ctx["scope"]
    ) == json.loads(orig_worker_result)
    with pytest.raises(ap.AutopilotError, match="unreviewed mode change"):
        validate_resolved_preparation_boundary(root, state_root)
    os.chmod(root / "code.py", stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)


def test_unexpected_errors_and_stale_live_facts_propagate(repo, monkeypatch):
    ctx = _setup_valid_boundary(repo, monkeypatch)
    root, state_root = ctx["root"], ctx["state_root"]
    facts = ctx["facts"]

    calls = [0]
    def mock_drifted_validate(r, require_clean=True):
        calls[0] += 1
        if calls[0] == 1:
            return dict(facts)
        return dict(facts, head="0" * 40)

    monkeypatch.setattr(ap, "validate_repository", mock_drifted_validate)
    with pytest.raises(ap.AutopilotError):
        validate_resolved_preparation_boundary(root, state_root)

    monkeypatch.setattr(ap, "validate_repository", lambda r, require_clean=True: dict(facts))

    marker = "UNEXPECTED_DEPENDENCY_RUNTIME_ERROR_99999"
    def mock_runtime_error(*args, **kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(runner, "bounded_file_text", mock_runtime_error)
    with pytest.raises(RuntimeError) as exc_rt:
        validate_resolved_preparation_boundary(root, state_root)
    assert marker in str(exc_rt.value)

    def mock_keyboard_interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(runner, "bounded_file_text", mock_keyboard_interrupt)
    with pytest.raises(KeyboardInterrupt):
        validate_resolved_preparation_boundary(root, state_root)

    leak_marker = "SECRET_INTERNAL_DISK_TOKEN_12345"
    def mock_oserror(*args, **kwargs):
        raise OSError(leak_marker)

    monkeypatch.setattr(runner, "bounded_file_text", mock_oserror)
    with pytest.raises(ap.AutopilotError) as exc_leak:
        validate_resolved_preparation_boundary(root, state_root)
    assert leak_marker not in str(exc_leak.value)
