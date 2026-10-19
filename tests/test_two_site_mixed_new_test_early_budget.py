"""CODEX-authored 737K: early canonical new-test bytes, not a new budget policy."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import copy
from contextlib import contextmanager
import json
from types import SimpleNamespace

import jsonschema
import pytest
from scripts import two_site_proposal_runner as runner
from scripts.two_site_correction_delta import build_candidate_sources, rebase_correction_result

CODE_ORIGIN = "CODEX"
SCRIPT = "scripts/unit_helper.py"
TEST = "tests/test_unit_helper.py"
PREFIX = "PROPOSAL_UTF8_BUDGET_V1:"


def text(size, letter="x"):
    """Valid Python comment with independently checked exact UTF-8 size."""
    width = len(letter.encode("utf-8"))
    count, tail = divmod(size - 2, width)
    value = "#" + letter * count + "x" * tail + "\n"
    assert len(value.encode("utf-8")) == size
    return value


def make_case(paths=None, limits=None, total=160000):
    paths = list(paths or [SCRIPT, TEST])
    task = {"task_id": "AE03-737K-UNIT", "goal": "Bounded synthetic byte policy proof.",
            "allowed_files": paths, "context_files": [], "requirements": ["Keep quality."],
            "test_selectors": [p for p in paths if p.startswith("tests/")]}
    if limits is not None:
        task["requirements"].append(PREFIX + json.dumps(
            {"version": "1.0", "file_limits": limits, "total_limit": total},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False))
    return task, {p: {"sha256": None, "content": None} for p in paths}


def result(task, sources, values, raw=False):
    changes = [{"path": p, "original_sha256": sources[p]["sha256"], "content": value}
               for p, value in values.items()]
    if raw:
        for change in changes:
            change["replacements"] = []
    return {"status": "PROPOSAL_READY", "summary": "Synthetic complete proposal.",
            "covered_requirements": list(range(1, len(task["requirements"]) + 1)),
            "findings": [], "changes": changes}


def expected_schema(task, sources, caps, forced=()):
    """Independent whole schema oracle for full-content branches in these cases."""
    branches = []
    for path in task["allowed_files"]:
        content = {"type": "string"}
        if path in forced:
            content["minLength"] = 1
        if path in caps:
            content["maxLength"] = caps[path]
        source_sha = sources[path]["sha256"]
        branches.append({"type": "object", "additionalProperties": False,
            "required": ["path", "original_sha256", "content", "replacements"],
            "properties": {"path": {"enum": [path]},
                "original_sha256": {"type": "null"} if source_sha is None else {"enum": [source_sha]},
                "content": content, "replacements": {"type": "array", "maxItems": 0}}})
    return {"type": "object", "additionalProperties": False,
        "required": ["status", "summary", "covered_requirements", "findings", "changes"],
        "properties": {
            "status": {"type": "string", "enum": ["PROPOSAL_READY", "BLOCKED"]},
            "summary": {"type": "string"},
            "covered_requirements": {"type": "array", "items": {"type": "integer"}},
            "findings": {"type": "array", "items": {"type": "string"}},
            "changes": {"type": "array", "items": {"anyOf": branches}}}}


def checked(task, active, value, origin=None):
    saved = copy.deepcopy((task, active, value, origin))
    try:
        answer = runner.validate_result(
            value, task, active, "worker", original_sources=origin)
        assert answer == value
        return answer
    finally:
        assert (task, active, value, origin) == saved


def reject(call, message):
    with pytest.raises(runner.ProposalError) as caught:
        call()
    assert type(caught.value) is runner.ProposalError
    assert str(caught.value) == message
    assert runner._provider_output_is_retryable(caught.value) is False


def stream(payload, mode="structured", model=None):
    envelope = {"conversation_id": "unit-fresh", "status": "SUCCESS"}
    if mode == "structured":
        envelope["structured_output"] = payload
    else:
        encoded = json.dumps(payload, ensure_ascii=False)
        envelope["response"] = encoded if mode == "text" else encoded + "\n" + encoded
    return "\n".join(json.dumps(event, ensure_ascii=False) for event in [
        {"event": "init", "conversation_id": "unit-fresh",
         "init": {"model": model or runner.MODEL}},
        {"event": "result", "result": envelope}])


def test_mixed_ascii_boundary_schema_whole_values_and_no_mutation():
    task, sources = make_case()
    saved = copy.deepcopy((task, sources))
    oracle = expected_schema(task, sources, {TEST: 24000})
    for _ in range(2):
        assert runner.schema("worker", task, sources) == oracle
    good = result(task, sources, {TEST: text(24000), SCRIPT: text(30000)})
    bad = result(task, sources, {TEST: text(24001), SCRIPT: text(30000)})
    assert checked(task, sources, good) == good
    reject(lambda: checked(task, sources, bad),
           "new test file content exceeds budget: " + TEST)
    assert checked(task, sources, good) == good
    assert runner.schema("worker", task, sources) == oracle
    assert (task, sources) == saved
    assert runner.schema("worker")["properties"]["status"]["enum"] == ["PROPOSAL_READY", "BLOCKED"]


@pytest.mark.parametrize("alias", ["tests/helper_test.py", "nested/test/test_alias.py"])
def test_legacy_test_only_alias_keeps_bytes_profile_and_correction_caps(alias):
    task, sources = make_case([TEST, alias])
    assert runner.schema("worker", task, sources) == expected_schema(
        task, sources, {TEST: 24000, alias: 24000})
    for letter in ("x", "é"):
        good = result(task, sources, {alias: text(24000, letter)})
        checked(task, sources, good)
        reject(lambda: checked(task, sources, result(task, sources,
               {alias: text(24001, letter)})), "new test file content exceeds budget: " + alias)
        checked(task, sources, good)
    lower, lower_sources = make_case([TEST, alias], {TEST: 24000, alias: 9000}, total=30000)
    assert runner.schema("worker", lower, lower_sources) == expected_schema(
        lower, lower_sources, {TEST: 24000, alias: 9000})
    good = result(lower, lower_sources, {alias: text(9000)})
    checked(lower, lower_sources, good)
    reject(lambda: checked(lower, lower_sources, result(lower, lower_sources,
           {alias: text(9001)})), "new test file content exceeds budget: " + alias)
    checked(lower, lower_sources, good)
    prior = result(task, sources, {alias: text(200)})
    active = build_candidate_sources(task["allowed_files"], sources, prior["changes"])
    force = runner.originally_new_paths(task, sources)
    saved = copy.deepcopy((task, sources, active))
    assert runner.schema("worker", task, active, force, original_sources=sources) == expected_schema(
        task, active, {TEST: 24000, alias: 24000}, force)
    delta = result(task, active, {alias: text(24000)})
    checked(task, active, delta, sources)
    reject(lambda: checked(task, active, result(task, active, {alias: text(24001)}), sources),
           "new test file content exceeds budget: " + alias)
    rebased = rebase_correction_result(delta, task["allowed_files"], sources, active)
    assert rebased == result(task, sources, {alias: text(24000)})
    checked(task, sources, rebased, sources)
    assert (task, sources, active) == saved


def test_missing_non_test_binding_preserves_legacy_error_order_and_new_cap():
    missing = "not_in_sources.py"
    task, sources = make_case([SCRIPT, TEST, missing])
    del sources[missing]
    sources[SCRIPT] = {"content": "value = 1\n",
                       "sha256": runner.sha(b"value = 1\n")}
    good = result(task, sources, {SCRIPT: "value = 2\n", TEST: text(24000)})
    frozen = copy.deepcopy((task, sources, good))
    faults = []
    for content, message, retryable in [
        ("value = 1\n", "empty or unchanged file", True),
        ("#\0unsafe\n", "unsafe generated content", False),
        ("KEY = 'sk-" + "s" * 30 + "'\n", "unsafe generated content", False),
    ]:
        faults.append((result(task, sources, {SCRIPT: content}), message, retryable))
    outside = result(task, sources, {SCRIPT: "value = 3\n"})
    outside["changes"][0]["path"] = "outside_assignment.py"
    faults.append((outside, "changed path is duplicated or outside assignment", False))
    for bad, message, retryable in faults:
        bad_before = copy.deepcopy(bad)
        assert checked(task, sources, good) == good
        with pytest.raises(runner.ProposalError) as caught:
            checked(task, sources, bad)
        assert type(caught.value) is runner.ProposalError
        assert str(caught.value) == message
        assert runner._provider_output_is_retryable(caught.value) is retryable
        assert checked(task, sources, good) == good
        assert (task, sources, good) == frozen and bad == bad_before
    reject(lambda: checked(task, sources, result(task, sources, {TEST: text(24001)})),
           "new test file content exceeds budget: " + TEST)
    assert checked(task, sources, good) == good
    assert (task, sources, good) == frozen


def test_unicode_is_bytes_not_schema_character_count():
    task, sources = make_case()
    schema = runner.schema("worker", task, sources)
    good = result(task, sources, {TEST: text(24000, "é")})
    bad = result(task, sources, {TEST: text(24001, "é")})
    assert len(bad["changes"][0]["content"]) < 24000
    jsonschema.validate(result(task, sources, {TEST: text(24001, "é")}, raw=True), schema)
    checked(task, sources, good)
    reject(lambda: checked(task, sources, bad),
           "new test file content exceeds budget: " + TEST)
    checked(task, sources, good)


def test_all_32_paths_not_trimmed_advisory_and_nested_canonical_test():
    paths = [SCRIPT] + [f"tests/test_unit_{i:02d}.py" for i in range(30)] + [
        "tests/nested/test_last.py"]
    task, sources = make_case(paths)
    notes = runner.task_preparation_notes(task, sources)
    assert len(notes["output_contract"]["new_test_files"]) <= 16
    omitted = next(p for p in paths[1:] if p not in notes["output_contract"]["new_test_files"])
    caps = {p: 24000 for p in paths[1:]}
    oracle = expected_schema(task, sources, caps)
    assert runner.schema("worker", task, sources) == oracle
    good = result(task, sources, {omitted: text(24000)})
    checked(task, sources, good)
    reject(lambda: checked(task, sources, result(task, sources, {omitted: text(24001)})),
           "new test file content exceeds budget: " + omitted)
    checked(task, sources, good)
    assert runner.schema("worker", task, sources) == oracle


def test_profile_lower_path_total_and_distinct_sibling_caps():
    other = "tests/test_sibling.py"
    task, sources = make_case([SCRIPT, TEST, other],
                         {SCRIPT: 160000, TEST: 6000, other: 12000}, total=10000)
    assert runner.schema("worker", task, sources) == expected_schema(
        task, sources, {TEST: 6000, other: 10000})
    for path, cap in [(TEST, 6000), (other, 10000)]:
        good = result(task, sources, {path: text(cap)})
        checked(task, sources, good)
        reject(lambda: checked(task, sources, result(task, sources, {path: text(cap + 1)})),
               "new test file content exceeds budget: " + path)
        checked(task, sources, good)
    # Aggregate all-change limits stay at the existing pre-review gate; this repair
    # must not silently impose the test cap/profile on unrelated new modules.
    checked(task, sources, result(task, sources, {SCRIPT: text(30000)}))
    checked(task, sources, result(task, sources, {TEST: text(6000), other: text(10000)}))


@pytest.mark.parametrize("fault", ["noncanonical", "duplicate", "wrong_paths", "zero"])
def test_malformed_profile_fails_closed_without_input_mutation(fault):
    task, sources = make_case(limits={SCRIPT: 10000, TEST: 10000}, total=12000)
    good = result(task, sources, {TEST: text(100)})
    checked(task, sources, good)
    broken = copy.deepcopy(task)
    if fault == "noncanonical":
        broken["requirements"][-1] = PREFIX + " " + broken["requirements"][-1][len(PREFIX):]
    elif fault == "duplicate":
        broken["requirements"].append(broken["requirements"][-1])
    elif fault == "wrong_paths":
        broken["requirements"][-1] = broken["requirements"][-1].replace(TEST, "tests/test_other.py")
    else:
        broken["requirements"][-1] = broken["requirements"][-1].replace('"total_limit":12000', '"total_limit":0')
    frozen = copy.deepcopy((broken, sources))
    reject(lambda: runner.schema("worker", broken, sources),
           "invalid proposal UTF-8 budget profile")
    reject(lambda: checked(broken, sources, result(broken, sources, {TEST: text(100)})),
           "invalid proposal UTF-8 budget profile")
    assert (broken, sources) == frozen
    checked(task, sources, good)


def test_schema_never_widens_existing_maxlength(monkeypatch):
    task, sources = make_case()
    original = runner.build_changes_schema
    def tighter(*args, **kwargs):
        value = original(*args, **kwargs)
        value["items"]["anyOf"][1]["properties"]["content"]["maxLength"] = 17
        return value
    with monkeypatch.context() as guarded:
        guarded.setattr(runner, "build_changes_schema", tighter)
        assert runner.schema("worker", task, sources) == expected_schema(task, sources, {TEST: 17})
    assert runner.schema("worker", task, sources) == expected_schema(task, sources, {TEST: 24000})


def test_existing_large_forced_full_never_becomes_new():
    task, sources = make_case()
    sources[TEST] = {"content": text(70000), "sha256": runner.sha(text(70000).encode())}
    force = (TEST,)
    oracle = expected_schema(task, sources, {}, force)
    assert runner.schema("worker", task, sources, force) == oracle
    value = result(task, sources, {TEST: text(70001, "z")})
    checked(task, sources, value, sources)
    raw = result(task, sources, {TEST: text(70001, "z")}, raw=True)
    normalized = runner.normalize_result(raw, task, sources, "worker", True, force)
    assert normalized == value
    checked(task, sources, normalized, sources)
    assert runner.schema("worker", task, sources, force) == oracle


@pytest.mark.parametrize("mode", ["structured", "text", "duplicate"])
def test_stream_mixed_oversize_is_terminal_not_retryable(mode):
    task, sources = make_case()
    good = result(task, sources, {TEST: text(24000)}, raw=True)
    bad = result(task, sources, {TEST: text(24001)}, raw=True)
    frozen = copy.deepcopy((task, sources, good, bad))
    expected = result(task, sources, {TEST: text(24000)})
    before, _ = runner._parse_stream(stream(good, mode), task, sources, "worker")
    assert before == expected
    reject(lambda: runner._parse_stream(stream(bad, mode), task, sources, "worker"),
           "new test file content exceeds budget: " + TEST)
    after, _ = runner._parse_stream(stream(good, mode), task, sources, "worker")
    assert after == before == expected
    assert (task, sources, good, bad) == frozen


@pytest.mark.parametrize("mode", ["structured", "text", "duplicate"])
def test_test_only_64k_raw_limit_unchanged_for_existing_replacements(mode):
    task, sources = make_case([TEST])
    base = "value = 1\n" + text(70000)
    sources[TEST] = {"content": base, "sha256": runner.sha(base.encode())}
    raw = result(task, sources, {})
    raw["changes"] = [{"path": TEST, "original_sha256": sources[TEST]["sha256"],
                      "content": None, "replacements": [{"old": "value = 1\n", "new": "value = 2\n"}]}]
    expected = result(task, sources, {TEST: base.replace("value = 1\n", "value = 2\n", 1)})
    first, _ = runner._parse_stream(stream(raw, mode), task, sources, "worker")
    assert first == expected and len(first["changes"][0]["content"].encode()) > 64000
    bad = copy.deepcopy(raw)
    bad["summary"] = "s" * 65000
    reject(lambda: runner._parse_stream(stream(bad, mode), task, sources, "worker"),
           "test-only raw transport exceeds budget")
    after, _ = runner._parse_stream(stream(raw, mode), task, sources, "worker")
    assert first == after == expected


def test_correction_active_sha_original_new_caps_and_rebased_retention():
    task, original = make_case()
    prior = result(task, original, {TEST: text(23999)})
    candidate = build_candidate_sources(task["allowed_files"], original, prior["changes"])
    force = runner.originally_new_paths(task, original)
    frozen = copy.deepcopy((task, original, candidate))
    oracle = expected_schema(task, candidate, {TEST: 24000}, force)
    assert runner.schema("worker", task, candidate, force, original_sources=original) == oracle
    good = result(task, candidate, {TEST: text(24000)})
    checked(task, candidate, good, original)
    bad = result(task, candidate, {TEST: text(24001)})
    reject(lambda: checked(task, candidate, bad, original),
           "new test file content exceeds budget: " + TEST)
    raw = result(task, candidate, {TEST: text(24001)}, raw=True)
    reject(lambda: runner._parse_stream(stream(raw), task, candidate, "worker", force,
                                        original_sources=original),
           "new test file content exceeds budget: " + TEST)
    rebased = rebase_correction_result(good, task["allowed_files"], original, candidate)
    assert rebased == result(task, original, {TEST: text(24000)})
    checked(task, original, rebased, original)
    # Defense in depth: a retained omitted new file is validated after rebase too.
    oversized = build_candidate_sources(task["allowed_files"], original,
                                       result(task, original, {TEST: text(24001)})["changes"])
    delta = result(task, oversized, {SCRIPT: text(100)})
    retained = rebase_correction_result(delta, task["allowed_files"], original, oversized)
    reject(lambda: checked(task, original, retained, original),
           "new test file content exceeds budget: " + TEST)
    checked(task, candidate, good, original)
    assert runner.schema("worker", task, candidate, force, original_sources=original) == oracle
    assert (task, original, candidate) == frozen


def test_actual_run_correction_blocks_before_result_and_ready(tmp_path, monkeypatch):
    """Real run/schema/parser/rebase/writers; fake only external CLI/profile/Git boundary."""
    task, original = make_case()
    prior = result(task, original, {TEST: text(200)})
    before_inputs = copy.deepcopy((task, original, prior))
    real_read = runner.bounded_file_text

    def one(label, size, fails):
        root = tmp_path / label
        root.mkdir()
        local = root / "proposals"
        local.mkdir()
        cli_home = root / "cli"
        cli_home.mkdir()
        prior_dir = root / "prior"
        prior_dir.mkdir()
        proposal = prior_dir / "result.json"
        raw_proposal = runner.serialize_json(prior).decode()
        proposal.write_text(raw_proposal)
        (prior_dir / "task.json").write_text(json.dumps(
            {"task": task, "source_head": "a" * 40, "sources": original}))
        (prior_dir / "status.json").write_text(json.dumps(
            {"role": "worker", "state": "PROPOSAL_READY", "run_id": "prior-worker",
             "conversation_id": "unit-prior", "result_sha256": runner.sha(raw_proposal.encode())}))
        feedback = root / "feedback.json"
        feedback.write_text(json.dumps({"reviewer": "test-gate",
            "reviewed_proposal_sha256": runner.sha(raw_proposal.encode()),
            "findings": ["Synthetic correction."], "test_evidence": []}))
        candidate = build_candidate_sources(task["allowed_files"], original, prior["changes"])
        payload = result(task, candidate, {TEST: text(size)}, raw=True)
        profile = SimpleNamespace(conversation_id="unit-fresh",
                                  workspace=cli_home / "proposal-runtime-workspace")
        @contextmanager
        def cli_context(*args):
            yield local, cli_home / "fake-cli", {"sha256": "b" * 64}
        class Process:
            returncode = 0
            pid = 12345
            def __init__(self, *args, **kwargs):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        def capture(process, prompt, output, errors, timeout):
            output.write_text(stream(payload, model=runner.WORKER_MODEL))
            errors.write_text("")
        def read(root_arg, path, limit, description):
            if description == "prompt template":
                return "Hermetic synthetic prompt."
            return real_read(root_arg, path, limit, description)
        with monkeypatch.context() as guard:
            guard.setattr(runner, "validate_runtime_contract", lambda *_: None)
            guard.setattr(runner, "load_task", lambda *_: (task, original))
            guard.setattr(runner, "load_worker_routing_policy", lambda *a, **k: SimpleNamespace(enabled=False))
            guard.setattr(runner, "select_worker_model_route", lambda *a, **k: (runner.WORKER_MODEL, runner.WORKER_EFFORT))
            guard.setattr(runner, "enrich_repository_dependency_context", lambda r, t, s, n: n)
            guard.setattr(runner, "validate_task_readiness", lambda *_: None)
            guard.setattr(runner, "git", lambda r, *a: "a" * 40 if a[:1] == ("rev-parse",) else "")
            guard.setattr(runner, "bounded_file_text", read)
            guard.setattr(runner, "proposal_cli_context", cli_context)
            guard.setattr(runner, "cli_identity", lambda *_: {"sha256": "b" * 64})
            guard.setattr(runner, "prepare_profile", lambda *a, **k: profile)
            guard.setattr(runner, "validate_profile", lambda *a, **k: {"conversation_id": "unit-fresh"})
            guard.setattr(runner.subprocess, "Popen", Process)
            guard.setattr(runner, "capture_bounded_process", capture)
            out = local / "unit-worker"
            call = lambda: runner.run(root, root / "task.json", "worker", "unit-worker", 30,
                                      feedback=feedback, proposal=proposal)
            if fails:
                reject(call, "new test file content exceeds budget: " + TEST)
                status = json.loads((out / "status.json").read_text())
                assert status["state"] == "BLOCKED"
                assert not (out / "result.json").exists()
                assert not (out / "delta-result.json").exists()
                assert not (out / "proposal.patch").exists()
            else:
                assert call() == out
                assert json.loads((out / "result.json").read_text()) == result(
                    task, original, {TEST: text(size)})
                assert json.loads((out / "status.json").read_text())["state"] == "PROPOSAL_READY"
            actual_schema = json.loads((out / "response-schema.json").read_text())
            assert actual_schema == expected_schema(
                task, candidate, {TEST: 24000}, runner.originally_new_paths(task, original))
        assert (task, original, prior) == before_inputs
    one("before", 24000, False)
    one("fault", 24001, True)
    one("after", 24000, False)
