"""Regression of the actual 267 byte-overrun factor; no retry authority."""
import copy
import hashlib
import json
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from scripts import two_site_proposal_pipeline as pipeline
from scripts.two_site_proposal_byte_profile import ProposalByteProfileError
from scripts.two_site_task_context import preparation_notes
from tests.test_two_site_pipeline_pre_review_gate import (
    build_test_receipt, fake_git,
    make_candidate_tester, make_runner,
)

TEST = "tests/test_budget.py"
PREFIX = "PROPOSAL_UTF8_BUDGET_V1:"


def notes_fixture(limits=None, total=40000):
    limits = limits or {TEST: 12000}
    profile = {"version": "1.0", "file_limits": limits, "total_limit": total}
    task = {"requirements": [PREFIX + json.dumps(profile, sort_keys=True, separators=(",", ":"))],
            "allowed_files": list(limits)}
    sources = {p: {"content": None, "sha256": None} for p in limits}
    return task, sources, profile


def test_notes_single_actual_cap_targets_detached_and_legacy_unchanged():
    task, sources, expected = notes_fixture()
    before = copy.deepcopy((task, sources, expected))
    result = preparation_notes(task, sources)["output_contract"]
    assert result == {"max_provider_output_tokens": 60000, "max_test_only_transport_bytes": 64000,
        "max_new_test_file_utf8_bytes": 12000, "test_only_task": True,
        "new_test_files": [TEST], "proposal_utf8_budget_profile": expected,
        "new_test_file_draft_target_utf8_bytes": 9000}
    result["proposal_utf8_budget_profile"]["file_limits"][TEST] = 1
    assert (task, sources, expected) == before
    assert preparation_notes(task, sources)["output_contract"]["max_new_test_file_utf8_bytes"] == 12000
    task["requirements"] = ["legacy"]
    assert preparation_notes(task, sources)["output_contract"] == {
        "max_provider_output_tokens": 60000, "max_test_only_transport_bytes": 64000,
        "max_new_test_file_utf8_bytes": 24000, "test_only_task": True, "new_test_files": [TEST]}


@pytest.mark.parametrize("cap,total,effective,target", [(30000,40000,24000,18000),
    (12000,8000,8000,6000), (1,40000,1,0)])
def test_notes_stricter_total_and_generic_cap(cap, total, effective, target):
    task, sources, _ = notes_fixture({TEST: cap}, total)
    before = copy.deepcopy((task, sources))
    result = preparation_notes(task, sources)["output_contract"]
    assert result["max_new_test_file_utf8_bytes"] == effective
    assert result["new_test_file_draft_target_utf8_bytes"] == target
    assert (task, sources) == before


def test_notes_multi_file_caps_do_not_restrict_permitted_larger_sibling():
    task, sources, expected = notes_fixture({TEST: 4000, "tests/test_large.py": 20000})
    result = preparation_notes(task, sources)["output_contract"]
    assert result["proposal_utf8_budget_profile"] == expected
    assert result["max_new_test_file_utf8_bytes"] == 24000
    assert "new_test_file_draft_target_utf8_bytes" not in result
    assert result["new_test_files"] == [TEST, "tests/test_large.py"]


@pytest.mark.parametrize("mutation", ["invalid", "duplicate", "path"])
def test_notes_bad_declaration_fails_without_mutation_and_restores(mutation):
    task, sources, _ = notes_fixture()
    good = preparation_notes(task, sources)
    bad = copy.deepcopy(task)
    if mutation == "invalid":
        bad["requirements"] = [PREFIX + "{}"]
    elif mutation == "duplicate":
        bad["requirements"] *= 2
    else:
        bad["allowed_files"] = ["tests/test_other.py"]
    snapshot = copy.deepcopy((bad, sources))
    with pytest.raises(ProposalByteProfileError, match="^invalid proposal UTF-8 budget profile$"):
        preparation_notes(bad, sources)
    assert (bad, sources) == snapshot
    assert preparation_notes(task, sources) == good


def test_notes_complete_32_path_profile_survives_advisory_trimming():
    limits = {f"tests/test_{'x' * 99}{i:02}.py": i + 1 for i in range(32)}
    task, sources, expected = notes_fixture(limits)
    task["goal"] = " ".join(f"DR-{i}" for i in range(40))
    before = copy.deepcopy((task, sources))
    a = preparation_notes(task, sources)
    b = preparation_notes(task, sources)
    assert a == b and (task, sources) == before
    assert len(json.dumps(a).encode()) <= 8192
    assert a["output_contract"]["proposal_utf8_budget_profile"] == expected


def receipt(actual=15853):
    return build_test_receipt(state="FAILED", returncode=1, stdout_tail="", snapshot_files=0,
        snapshot_bytes=0, isolation="static-byte-profile",
        stderr_tail=f"AssertionError: {TEST} exceeds 12000 byte limit: {actual} bytes")


def independent_expected_signature():
    payload = {"returncode": 1, "selectors": ["tests/test_two_site_pipeline_pre_review_gate.py"],
        "facts": {"byte_budget_factors": [f"{TEST} exceeds 12000 byte limit"]},
        "isolation": "static-byte-profile"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode()).hexdigest()


def test_actual_three_size_variants_one_factor_exact_feedback_retained():
    expected = independent_expected_signature()
    for size in (15853, 15847, 15916):
        r = receipt(size)
        before = copy.deepcopy(r)
        assert pipeline.test_failure_signature(r) == expected
        assert pipeline.test_gate_feedback(r, r["proposal_sha256"])["test_evidence"][-1] == r["stderr_tail"]
        assert r == before


@pytest.mark.parametrize("mutation", ["path", "cap", "total", "selector", "generic", "invalid",
    "duplicate", "noncanonical", "nonoverrun", "stdout", "snapshot", "returncode"])
def test_distinct_factors_not_collapsed_and_restore(mutation):
    good = receipt()
    expected = independent_expected_signature()
    assert pipeline.test_failure_signature(good) == expected
    bad = copy.deepcopy(good)
    if mutation == "path":
        bad["stderr_tail"] = bad["stderr_tail"].replace(TEST, "tests/test_other.py")
    elif mutation == "cap":
        bad["stderr_tail"] = bad["stderr_tail"].replace("12000", "13000")
    elif mutation == "total":
        bad["stderr_tail"] = bad["stderr_tail"].replace(TEST, "proposal total")
    elif mutation == "selector":
        bad["selectors"] = ["tests/test_other.py"]
    elif mutation == "generic":
        bad["isolation"] = "bwrap"
    elif mutation == "invalid":
        bad["stderr_tail"] += " unexpected"
    elif mutation == "duplicate":
        bad["stderr_tail"] += "\n" + bad["stderr_tail"]
    elif mutation == "noncanonical":
        bad["stderr_tail"] = bad["stderr_tail"].replace(TEST, "../tests/test_budget.py")
    elif mutation == "nonoverrun":
        bad["stderr_tail"] = bad["stderr_tail"].replace("15853", "12000")
    elif mutation == "stdout":
        bad["stdout_tail"] = "unexpected"
    elif mutation == "snapshot":
        bad["snapshot_files"] = 1
    else:
        bad["returncode"] = 2
    snapshot = copy.deepcopy(bad)
    assert pipeline.test_failure_signature(bad) != expected
    assert bad == snapshot
    assert pipeline.test_failure_signature(good) == expected


def test_all_file_and_total_factors_order_invariant_but_total_cap_distinct():
    a = receipt()
    a["stderr_tail"] += "\nAssertionError: scripts/source.py exceeds 8000 byte limit: 9000 bytes"
    a["stderr_tail"] += "\nAssertionError: proposal total exceeds 20000 byte limit: 24853 bytes"
    b = copy.deepcopy(a)
    b["stderr_tail"] = "\n".join(reversed(a["stderr_tail"].splitlines())).replace("24853", "24847")
    before = copy.deepcopy((a, b))
    signature = pipeline.test_failure_signature(a)
    assert pipeline.test_failure_signature(b) == signature
    b["stderr_tail"] = b["stderr_tail"].replace("20000", "21000")
    assert pipeline.test_failure_signature(b) != signature
    b = before[1]
    assert pipeline.test_failure_signature(b) == signature and a == before[0]


def test_semantic_pytest_numbers_remain_distinct():
    a, b = receipt(15853), receipt(15847)
    a["isolation"] = b["isolation"] = "bwrap"
    assert pipeline.test_failure_signature(a) != pipeline.test_failure_signature(b)


def _run_loop(templates, prefix):
    with TemporaryDirectory() as directory:
        case = SimpleNamespace(root=Path(directory).resolve(), state={})
        case.task_path = case.root / "task.json"
        (case.root / "src.py").write_text("# orig\n")
        task = {"task_id": "T-PRE-REVIEW-GATE", "goal": "Gate regression tests",
            "context_files": ["src.py"], "allowed_files": ["src.py"], "requirements": ["R1"],
            "test_selectors": ["tests/test_two_site_pipeline_pre_review_gate.py"]}
        pipeline.write_json(case.task_path, task)
        reservation = {"fingerprint": "0" * 64}
        runner, calls = make_runner(case.root, [{"content": "# v1\n"}, {"content": "# v2\n"},
            {"status": "VERIFIED"}])
        tester, tests = make_candidate_tester(templates)
        with (patch.object(pipeline, "git", side_effect=fake_git),
              patch.object(pipeline, "runner_run", side_effect=runner),
              patch.object(pipeline, "run_candidate_tests", side_effect=tester),
              patch.object(pipeline, "reserve_semantic_attempt", return_value=reservation)):
            if templates[-1]["state"] == "FAILED":
                with pytest.raises(pipeline.ProposalError, match="^repeated test failure signature:"):
                    pipeline.run_pipeline(case.root, case.task_path, prefix, 300, None, case.state)
                assert [c[0] for c in calls] == ["worker", "worker"]
                assert len(tests) == 2 and case.state["correction_count"] == 1
                evidence = case.root / ".local/two-site-refactor/pipeline" / prefix
                assert not (evidence / "feedback-correction-2.json").exists()
                for i, source in enumerate(templates):
                    kept = json.loads((evidence / f"pre-review-tests-{i}.json").read_text())
                    assert kept["stderr_tail"] == source["stderr_tail"]
                    assert kept["state"] == "FAILED"
                return
            result = pipeline.run_pipeline(case.root, case.task_path, prefix, 300, None, case.state)
            assert result["state"] == "READY_FOR_CODEX" and result["correction_count"] == 1
            assert [c[0] for c in calls] == ["worker", "worker", "verifier"]
            assert len(tests) == 2


def test_loop_same_factor_blocks_worker2_and_reviewer_then_real_fixed_receipt_proceeds():
    good = [receipt(), build_test_receipt()]
    before = copy.deepcopy(good)
    _run_loop(good, "byte-good-before")
    bad = [receipt(15853), receipt(15847)]
    snapshot = copy.deepcopy(bad)
    _run_loop(bad, "byte-repeat")
    assert bad == snapshot
    _run_loop(good, "byte-good-after")
    assert good == before
