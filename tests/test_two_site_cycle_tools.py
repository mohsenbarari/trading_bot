"""Real temporary-Git fixtures for task provenance and review safety."""
import json
import os
import subprocess

import pytest

from scripts import two_site_cycle_tools as ct
from scripts import two_site_proposal_pipeline as pipeline
from scripts import two_site_proposal_runner as runner


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.two_site_semantic_task_builder.load_active_assignment_scope", lambda *args, **kwargs: "stage_a")
    try:
        import two_site_semantic_task_builder
        monkeypatch.setattr(two_site_semantic_task_builder, "load_active_assignment_scope", lambda *args, **kwargs: "stage_a")
    except (ImportError, AttributeError):
        pass
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], stderr=subprocess.DEVNULL, text=True).strip()
    git("init")
    git("config", "user.email", "offline@example.invalid")
    git("config", "user.name", "Offline Fixture")
    (tmp_path / ".gitignore").write_text(".local/\n")
    (tmp_path / "gap.md").write_text("# Gap A\nCharacterize method f.\n")
    (tmp_path / "code.py").write_text("import os\nLIMIT = 2\n@staticmethod\ndef f(x):\n    return x + LIMIT\n\ndef unrelated():\n    return 1\n")
    (tmp_path / "tests").mkdir()
    git("add", "--", ".gitignore", "gap.md", "code.py")
    git("commit", "-m", "offline fixture")
    return tmp_path, git


def task():
    return {"task_id": "GAP-A-001", "goal": "Test method f.", "context_files": ["gap.md"],
            "allowed_files": ["tests/test_new.py"], "requirements": ["1. Characterize method f"],
            "test_selectors": ["tests/test_new.py"]}


def p1_00b_batch_task() -> dict:
    test_path = "tests/test_p1_00b_characterization_user_flags_batch.py"
    return {
        "task_id": "P1-00B-BATCH-USER-FLAGS-001",
        "goal": "WA-P1-00B-FREEZE-20260904-001: characterize compatible user-flag handlers.",
        "context_files": ["gap.md"],
        "allowed_files": [test_path],
        "requirements": [
            "[HANDLER] api/routers/user_flags.py:resolve_user_flag | "
            f"selector={test_path}::UserFlagBatchTests::test_resolve_user_flag_not_found | scenario=not_found",
            "[HANDLER] api/routers/user_flags.py:list_open_user_flags | "
            f"selector={test_path}::UserFlagBatchTests::test_list_open_user_flags_authority | scenario=authority",
        ],
        "test_selectors": [
            f"{test_path}::UserFlagBatchTests::test_resolve_user_flag_not_found",
            f"{test_path}::UserFlagBatchTests::test_list_open_user_flags_authority",
        ],
    }


def test_p1_00b_batch_contract_accepts_independent_handler_receipts():
    ct.validate_p1_00b_batch_spec(p1_00b_batch_task(), "P1-00B-BATCH-V1.user-flags")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("mixed_source", "share one source module"),
        ("duplicate_handler", "repeats a handler scenario"),
        ("selector_drift", "selectors must exactly match"),
        ("single_handler", "two to four explicit handler requirements"),
    ],
)
def test_p1_00b_batch_contract_rejects_ambiguous_evidence(mutation, message):
    value = p1_00b_batch_task()
    if mutation == "mixed_source":
        value["requirements"][1] = value["requirements"][1].replace(
            "api/routers/user_flags.py:list_open_user_flags", "api/routers/users.py:list_users"
        )
    elif mutation == "duplicate_handler":
        value["requirements"][1] = value["requirements"][0]
        value["test_selectors"][1] = value["test_selectors"][0]
    elif mutation == "selector_drift":
        value["test_selectors"][1] = "tests/test_other.py::Other::test_other"
    else:
        value["requirements"] = value["requirements"][:1]
        value["test_selectors"] = value["test_selectors"][:1]
    with pytest.raises(runner.ProposalError, match=message):
        ct.validate_p1_00b_batch_spec(value, "P1-00B-BATCH-V1.user-flags")


def test_non_batch_p1_00b_task_remains_compatible():
    value = p1_00b_batch_task()
    ct.validate_p1_00b_batch_spec(value, "P1-00B-SINGLE-V1.user-flags")


def test_p1_00b_batch_builder_and_runtime_recheck_contract(repo):
    root, _ = repo
    spec = root / "batch-spec.json"
    spec.write_text(json.dumps({
        "task": p1_00b_batch_task(),
        "gap_reference": "gap.md#p1-00b-user-flags",
        "snippets": [{"path": "code.py", "symbols": ["f"]}],
        "scenario_key": "P1-00B-BATCH-V1.user-flags",
    }))
    output = root / ".local/two-site-refactor/tasks/p1-00b-batch.json"
    assert ct.build(root, spec, output)["state"] == "TASK_READY"
    assert runner.load_task(root, output)[0]["task_id"] == "P1-00B-BATCH-USER-FLAGS-001"

    tampered = json.loads(output.read_text())
    tampered["requirements"] = tampered["requirements"][:1]
    output.write_text(json.dumps(tampered))
    with pytest.raises(runner.ProposalError, match="two to four explicit handler requirements"):
        runner.load_task(root, output)


def built(repo, snippets=None, scenario_key="scenario_a"):
    root, _ = repo
    spec = root / "spec.json"
    spec_data = {"task": task(), "gap_reference": "gap.md#gap-a",
                 "snippets": snippets if snippets is not None else [{"path": "code.py", "symbols": ["f", "LIMIT"]}]}
    if scenario_key is not None:
        spec_data["scenario_key"] = scenario_key
    spec.write_text(json.dumps(spec_data))
    output = root / ".local/two-site-refactor/tasks/new.json"
    value = ct.build(root, spec, output)
    spec.unlink()
    return output, value


def test_builder_proves_symbols_and_reduces_full_sources(repo):
    root, _ = repo
    output, result = built(repo)
    assert result["state"] == "TASK_READY"
    assert "semantic_task_fingerprint" in result
    assert isinstance(result["semantic_task_fingerprint"], str) and len(result["semantic_task_fingerprint"]) == 64
    loaded, sources = runner.load_task(root, output)
    package = json.loads(loaded["goal"].split(ct.MARKER, 1)[1])
    assert "semantic_identity" in package
    assert package["semantic_identity"]["fingerprint"] == result["semantic_task_fingerprint"]
    assert "code.py" not in sources
    assert "@staticmethod\ndef f" in loaded["goal"].replace("\\n", "\n")
    assert "unrelated" not in loaded["goal"]
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        ct.build(root, _write_spec(root), output)


def _write_spec(root):
    path = root / "spec.json"
    path.write_text(json.dumps({"task": task(), "snippets": [], "gap_reference": "gap.md#gap-a", "scenario_key": "scenario_a"}))
    return path


def test_missing_scenario_is_rejected(repo):
    root, _ = repo
    spec = root / "spec.json"
    spec.write_text(json.dumps({"task": task(), "snippets": [], "gap_reference": "gap.md#gap-a"}))
    output = root / ".local/two-site-refactor/tasks/missing_scenario.json"
    with pytest.raises(runner.ProposalError, match="scenario_key"):
        ct.build(root, spec, output)


@pytest.mark.parametrize("mutation", [
    "source", "head", "text", "range", "hash", "symbol",
    "identity_removal", "forged_fingerprint", "forged_manifest",
    "stage_drift", "scenario_drift",
])
def test_excerpt_drift_is_rejected(repo, monkeypatch, mutation):
    root, git = repo
    output, _ = built(repo)
    if mutation == "source":
        with (root / "code.py").open("a") as file:
            file.write("# drift\n")
    elif mutation == "head":
        git("commit", "--allow-empty", "-m", "new head")
    elif mutation == "stage_drift":
        monkeypatch.setattr("scripts.two_site_semantic_task_builder.load_active_assignment_scope", lambda *args, **kwargs: "stage_b")
        try:
            import two_site_semantic_task_builder
            monkeypatch.setattr(two_site_semantic_task_builder, "load_active_assignment_scope", lambda *args, **kwargs: "stage_b")
        except (ImportError, AttributeError):
            pass
    else:
        value = json.loads(output.read_text())
        goal, raw = value["goal"].split(ct.MARKER)
        package = json.loads(raw)
        if mutation == "identity_removal":
            package.pop("semantic_identity", None)
        elif mutation == "forged_fingerprint":
            package["semantic_identity"]["fingerprint"] = "0" * 64
        elif mutation == "forged_manifest":
            package["semantic_identity"]["source_manifest"]["gap.md"] = "0" * 64
        elif mutation == "scenario_drift":
            package["semantic_identity"]["scenario_key"] = "scenario_b"
        else:
            item = package["excerpts"][0]
            if mutation == "text": item["ranges"][0]["text"] = "import fake\n"
            if mutation == "range": item["ranges"][0]["start"] = 7
            if mutation == "hash": item["sha256"] = "0" * 64
            if mutation == "symbol": item["symbols"] = ["missing"]
        value["goal"] = goal + ct.MARKER + json.dumps(package)
        output.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        runner.load_task(root, output)


@pytest.mark.parametrize("snippets", [
    [{"path": ".local/f.py", "symbols": ["f"]}],
    [{"path": "code.py", "symbols": ["no_such_symbol"]}],
    [{"path": "../code.py", "symbols": ["f"]}],
    [{"path": "code.py", "symbols": ["f"]}] * 2,
])
def test_invalid_excerpts_rejected_before_provider(repo, snippets):
    with pytest.raises((ValueError, subprocess.SubprocessError)):
        built(repo, snippets)


def test_excerpt_links_and_secrets_rejected(repo):
    root, _ = repo
    os.link(root / "code.py", root / "linked.py")
    with pytest.raises(ValueError):
        ct.excerpt(root, "code.py", ["f"])
    (root / "linked.py").unlink()
    with (root / "code.py").open("a") as file:
        file.write("# " + "sk-" + "x" * 25 + "\n")
    with pytest.raises(ValueError):
        ct.excerpt(root, "code.py", ["f"])


def chain(repo):
    root, git = repo
    output, _ = built(repo, [])
    task_value, sources = runner.load_task(root, output)
    head = git("rev-parse", "HEAD")
    base = root / ".local/two-site-refactor/proposals"
    worker = base / "w1"
    reviewer = base / "r1"
    worker.mkdir(parents=True)
    reviewer.mkdir()
    result = {"status": "PROPOSAL_READY", "summary": "test added", "covered_requirements": [1],
              "findings": [], "changes": [{"path": "tests/test_new.py", "original_sha256": None,
                                             "content": "def test_new():\n    assert True\n"}]}
    snapshot = {"source_head": head, "task": task_value, "sources": sources}
    runner.write_json(worker / "result.json", result)
    digest = runner.file_sha256(worker / "result.json")
    runner.write_json(worker / "task.json", snapshot)
    runner.write_json(worker / "status.json", {"run_id": "w1", "role": "worker", "head": head,
        "model": runner.WORKER_MODEL, "state": "PROPOSAL_READY", "conversation_id": "wc", "result_sha256": digest})
    runner.write_json(reviewer / "result.json", {"status": "VERIFIED", "summary": "reviewed", "covered_requirements": [1], "findings": [], "changes": []})
    runner.write_json(reviewer / "task.json", {**snapshot, "proposal": (worker / "result.json").read_text()})
    runner.write_json(reviewer / "status.json", {"run_id": "r1", "role": "verifier", "head": head,
        "model": runner.MODEL, "state": "VERIFIED", "conversation_id": "rc", "result_sha256": runner.file_sha256(reviewer / "result.json"),
        "input_worker_run_id": "w1", "input_proposal_sha256": digest, "input_worker_conversation_id": "wc"})
    return output, digest, result, reviewer


def test_review_never_claims_tests_or_semantic_acceptance(repo):
    root, _ = repo
    output, digest, result, _ = chain(repo)
    receipt = ct.review(root, output, "w1", "r1", digest)
    assert receipt["state"] == "BINDINGS_VALID"
    assert receipt["tests"] == "NOT_RUN"
    (root / "tests/test_new.py").write_text(result["changes"][0]["content"])
    receipt = ct.review(root, output, "w1", "r1", digest, True)
    assert receipt["state"] == "BYTES_VALID"
    assert receipt["semantic_review"] == "REQUIRED_BY_CODEX"


@pytest.mark.parametrize("mutation", ["digest", "conversation", "head", "task", "source", "bytes", "mode", "extra", "hardlink"])
def test_review_rejects_wrong_bindings_and_dirty_bytes(repo, mutation):
    root, git = repo
    output, digest, result, reviewer = chain(repo)
    applied = mutation in {"bytes", "mode", "extra", "hardlink", "source"}
    if applied:
        path = root / "tests/test_new.py"
        path.write_text(result["changes"][0]["content"])
        if mutation == "bytes": path.write_text("different\n")
        if mutation == "mode": path.chmod(0o755)
        if mutation == "extra": (root / "unexpected.py").write_text("oops\n")
        if mutation == "hardlink": os.link(path, root / ".local/link.py")
        if mutation == "source": (root / "gap.md").write_text("changed\n")
    if mutation == "digest": digest = "0" * 64
    if mutation == "conversation":
        status = json.loads((reviewer / "status.json").read_text())
        status["conversation_id"] = "wc"
        runner.write_json(reviewer / "status.json", status)
    if mutation == "head": git("commit", "--allow-empty", "-m", "head drift")
    if mutation == "task":
        value = json.loads(output.read_text())
        value["requirements"].append("extra")
        output.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        ct.review(root, output, "w1", "r1", digest, applied)


@pytest.mark.parametrize("reuse_conversation", [False, True])
def test_correction_worker_requires_fresh_session_and_exact_prior(repo, reuse_conversation):
    root, _ = repo
    output, digest, result, reviewer = chain(repo)
    worker = reviewer.parent / "w1"
    prior = reviewer.parent / "w0"
    prior.mkdir()
    snapshot = json.loads((worker / "task.json").read_text())
    previous = {**result, "changes": [{**result["changes"][0], "content": "def test_new():\n    assert False\n"}]}
    runner.write_json(prior / "result.json", previous)
    prior_text = (prior / "result.json").read_text()
    prior_sha = runner.sha(prior_text.encode())
    runner.write_json(prior / "task.json", snapshot)
    status = json.loads((worker / "status.json").read_text())
    runner.write_json(prior / "status.json", {**status, "run_id": "w0", "result_sha256": prior_sha, "conversation_id": "wc0"})
    feedback = {"reviewer": "codex", "reviewed_proposal_sha256": prior_sha,
                "findings": ["Assertion must reflect true behavior"], "test_evidence": []}
    runner.write_json(worker / "task.json", {**snapshot, "proposal": prior_text, "feedback": feedback})
    status.update(input_worker_run_id="w0", input_proposal_sha256=prior_sha, input_worker_conversation_id="wc0")
    if reuse_conversation:
        status["conversation_id"] = "wc0"
        reviewed_status = json.loads((reviewer / "status.json").read_text())
        reviewed_status["input_worker_conversation_id"] = "wc0"
        runner.write_json(reviewer / "status.json", reviewed_status)
    runner.write_json(worker / "status.json", status)
    if reuse_conversation:
        with pytest.raises(ValueError, match="reused conversation"):
            ct.review(root, output, "w1", "r1", digest)
    else:
        assert ct.review(root, output, "w1", "r1", digest)["state"] == "BINDINGS_VALID"


@pytest.mark.parametrize("forged_prior", [False, True])
def test_correction_worker_model_routing_and_forged_prior_gate(repo, monkeypatch, forged_prior):
    root, _ = repo
    opus_model = "claude-opus-4-6-thinking"
    output, digest, result, reviewer = chain(repo)
    worker = reviewer.parent / "w1"
    prior = reviewer.parent / "w0"
    prior.mkdir()
    snapshot = json.loads((worker / "task.json").read_text())
    expected_task_id = snapshot["task"]["task_id"]

    calls = []

    def fake_expected_role_model(role, task_id):
        assert task_id == expected_task_id
        calls.append((role, task_id))
        if role == "worker":
            return opus_model
        return runner.MODEL

    monkeypatch.setattr(pipeline, "expected_role_model", fake_expected_role_model)

    previous = {**result, "changes": [{**result["changes"][0], "content": "def test_new():\n    assert False\n"}]}
    runner.write_json(prior / "result.json", previous)
    prior_text = (prior / "result.json").read_text()
    prior_sha = runner.sha(prior_text.encode())
    runner.write_json(prior / "task.json", snapshot)

    status = json.loads((worker / "status.json").read_text())
    prior_model = runner.MODEL if forged_prior else opus_model
    runner.write_json(
        prior / "status.json",
        {**status, "run_id": "w0", "model": prior_model, "result_sha256": prior_sha, "conversation_id": "wc0"},
    )
    feedback = {
        "reviewer": "codex",
        "reviewed_proposal_sha256": prior_sha,
        "findings": ["Assertion must reflect true behavior"],
        "test_evidence": [],
    }
    runner.write_json(worker / "task.json", {**snapshot, "proposal": prior_text, "feedback": feedback})
    status.update(
        model=opus_model,
        input_worker_run_id="w0",
        input_proposal_sha256=prior_sha,
        input_worker_conversation_id="wc0",
    )
    runner.write_json(worker / "status.json", status)

    if forged_prior:
        with pytest.raises(runner.ProposalError, match="prior identity mismatch"):
            ct.review(root, output, "w1", "r1", digest)
        assert calls == [("worker", expected_task_id), ("worker", expected_task_id)]
    else:
        receipt = ct.review(root, output, "w1", "r1", digest)
        assert receipt["state"] == "BINDINGS_VALID"
        assert calls == [
            ("worker", expected_task_id),
            ("worker", expected_task_id),
            ("worker", expected_task_id),
            ("verifier", expected_task_id),
        ]


def test_review_rejects_pre_apply_dirty_tree(repo):
    root, _ = repo
    output, digest, _, _ = chain(repo)
    (root / "untracked.txt").write_text("dirty\n")
    with pytest.raises(runner.ProposalError, match="pre-apply review requires clean Git"):
        ct.review(root, output, "w1", "r1", digest, applied=False)


def test_review_rejects_staged_changes_in_post_apply(repo):
    root, git = repo
    output, digest, result, _ = chain(repo)
    (root / "tests/test_new.py").write_text(result["changes"][0]["content"])
    git("add", "--", "tests/test_new.py")
    with pytest.raises(runner.ProposalError, match="post-apply check must precede staging"):
        ct.review(root, output, "w1", "r1", digest, applied=True)


@pytest.mark.parametrize("aliased_parent", [False, True])
def test_build_rejects_escaped_or_aliased_output_parent(repo, aliased_parent):
    root, _ = repo
    spec = _write_spec(root)
    output = root / "outside-task.json"
    if aliased_parent:
        parent = root / ".local/two-site-refactor"
        parent.mkdir(parents=True)
        destination = root / "destination"
        destination.mkdir()
        (parent / "tasks").symlink_to(destination, target_is_directory=True)
        output = parent / "tasks/new.json"
    with pytest.raises(runner.ProposalError, match="fresh bounded ignored task JSON"):
        ct.build(root, spec, output)
    assert not output.exists()


def make_receipt(proposal_sha, state="PASSED", returncode=0):
    return {
        "state": state,
        "proposal_sha256": proposal_sha,
        "selectors": ["tests/test_new.py"],
        "unsupported_specs": [],
        "returncode": returncode,
        "stdout_tail": "all tests passed\n",
        "stderr_tail": "",
        "snapshot_files": 1,
        "snapshot_bytes": 100,
        "isolation": "bubblewrap",
    }


def test_review_legacy_chain_without_test_evidence_passes(repo):
    root, _ = repo
    output, digest, result, _ = chain(repo)
    receipt = ct.review(root, output, "w1", "r1", digest)
    assert receipt["state"] == "BINDINGS_VALID"
    assert receipt["tests"] == "NOT_RUN"
    (root / "tests/test_new.py").write_text(result["changes"][0]["content"])
    applied = ct.review(root, output, "w1", "r1", digest, applied=True)
    assert applied["state"] == "BYTES_VALID"


def test_review_accepts_sha_bound_passed_test_evidence(repo):
    root, _ = repo
    output, digest, result, reviewer = chain(repo)
    evidence = make_receipt(digest, state="PASSED", returncode=0)
    evidence_sha = runner.sha(runner.serialize_json(evidence))

    task_json = json.loads((reviewer / "task.json").read_text())
    task_json["test_evidence"] = evidence
    runner.write_json(reviewer / "task.json", task_json)

    status_json = json.loads((reviewer / "status.json").read_text())
    status_json["input_test_evidence_sha256"] = evidence_sha
    runner.write_json(reviewer / "status.json", status_json)

    receipt = ct.review(root, output, "w1", "r1", digest)
    assert receipt["state"] == "BINDINGS_VALID"
    assert receipt["tests"] == "NOT_RUN"

    (root / "tests/test_new.py").write_text(result["changes"][0]["content"])
    applied = ct.review(root, output, "w1", "r1", digest, applied=True)
    assert applied["state"] == "BYTES_VALID"


@pytest.mark.parametrize("asymmetric_mode", ["snapshot_only", "status_only"])
def test_review_rejects_asymmetric_test_evidence(repo, asymmetric_mode):
    root, _ = repo
    output, digest, _, reviewer = chain(repo)
    evidence = make_receipt(digest, state="PASSED", returncode=0)
    evidence_sha = runner.sha(runner.serialize_json(evidence))

    if asymmetric_mode == "snapshot_only":
        task_json = json.loads((reviewer / "task.json").read_text())
        task_json["test_evidence"] = evidence
        runner.write_json(reviewer / "task.json", task_json)
    else:
        status_json = json.loads((reviewer / "status.json").read_text())
        status_json["input_test_evidence_sha256"] = evidence_sha
        runner.write_json(reviewer / "status.json", status_json)

    with pytest.raises(ValueError):
        ct.review(root, output, "w1", "r1", digest)


def test_review_rejects_wrong_evidence_sha(repo):
    root, _ = repo
    output, digest, _, reviewer = chain(repo)
    evidence = make_receipt(digest, state="PASSED", returncode=0)

    task_json = json.loads((reviewer / "task.json").read_text())
    task_json["test_evidence"] = evidence
    runner.write_json(reviewer / "task.json", task_json)

    status_json = json.loads((reviewer / "status.json").read_text())
    status_json["input_test_evidence_sha256"] = "a" * 64
    runner.write_json(reviewer / "status.json", status_json)

    with pytest.raises(ValueError):
        ct.review(root, output, "w1", "r1", digest)


@pytest.mark.parametrize("invalid_receipt_case", ["failed", "mismatched_proposal_sha"])
def test_review_rejects_failed_or_mismatched_test_receipt(repo, invalid_receipt_case):
    root, _ = repo
    output, digest, _, reviewer = chain(repo)
    if invalid_receipt_case == "failed":
        evidence = make_receipt(digest, state="FAILED", returncode=1)
    else:
        evidence = make_receipt("b" * 64, state="PASSED", returncode=0)
    evidence_sha = runner.sha(runner.serialize_json(evidence))

    task_json = json.loads((reviewer / "task.json").read_text())
    task_json["test_evidence"] = evidence
    runner.write_json(reviewer / "task.json", task_json)

    status_json = json.loads((reviewer / "status.json").read_text())
    status_json["input_test_evidence_sha256"] = evidence_sha
    runner.write_json(reviewer / "status.json", status_json)

    with pytest.raises(ValueError):
        ct.review(root, output, "w1", "r1", digest)


def test_review_rejects_free_form_or_non_dict_test_evidence(repo):
    root, _ = repo
    output, digest, _, reviewer = chain(repo)

    task_json = json.loads((reviewer / "task.json").read_text())
    task_json["test_evidence"] = "free-form string evidence"
    runner.write_json(reviewer / "task.json", task_json)
    status_json = json.loads((reviewer / "status.json").read_text())
    status_json["input_test_evidence_sha256"] = "c" * 64
    runner.write_json(reviewer / "status.json", status_json)

    with pytest.raises(ValueError):
        ct.review(root, output, "w1", "r1", digest)

    free_form = {"status": "passed", "selectors": ["test_new.py"]}
    task_json["test_evidence"] = free_form
    runner.write_json(reviewer / "task.json", task_json)
    status_json["input_test_evidence_sha256"] = runner.sha(runner.serialize_json(free_form))
    runner.write_json(reviewer / "status.json", status_json)

    with pytest.raises(ValueError):
        ct.review(root, output, "w1", "r1", digest)
