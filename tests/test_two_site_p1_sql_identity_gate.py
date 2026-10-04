"""Private AE03 regression: narrow P1 SQLAlchemy identity gate."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from scripts import two_site_proposal_pipeline as pipeline

from scripts import two_site_pre_review_receipt as gate
PATH = "tests/test_p1_00b_characterization_notifications.py"
SHA = "a" * 64


def worker(content, path=PATH, original_sha256=None):
    return {"changes": [{"path": path, "original_sha256": original_sha256, "content": content}]}


@pytest.mark.parametrize("source", [
    "self.assertIs(id_clause.left, Notification.__table__.c.id)",
    "self.assertIs(Notification.__table__.c.id, id_clause.left)",
    "self.assertIs(id_clause.left, Notification.__table__.c.id, 'column')",
    "self.assertIs(first=id_clause.left, second=Notification.__table__.c.id)",
    "self.assertIs(id_clause.left, Notification.__table__.c['id'])",
    "assert id_clause.left is Notification.__table__.c.id",
    "assert Notification.__table__.c.id is id_clause.left",
    "assert True and id_clause.left is Notification.__table__.c.id",
    "assert x is id_clause.left is Notification.__table__.c.id",
])
def test_exact_known_identity_defect_is_reported(source):
    candidate = worker("def test_sql(self):\n    " + source + "\n")
    before = copy.deepcopy(candidate)
    findings = gate.candidate_test_assertion_findings(candidate)
    assert findings == [PATH + ":2: sqlalchemy-column-object-identity; use exact compiled SQL "
                        "and column-specific parameter mapping, not Python identity of wrapped columns."]
    assert candidate == before


@pytest.mark.parametrize("source", [
    "self.assertEqual(compiled.params, {'id_1': 999, 'user_id_1': 5})",
    "assert compiled.params == {'id_1': 999, 'user_id_1': 5}",
    "self.assertIs(Notification.__table__.c.id, original_columns[0])",
    "assert Notification.__table__.c.id is original_columns[0]",
    "self.assertIs(result, None)",
    "assert result is None",
    "assert id_clause.left is not Notification.__table__.c.id",
    "self.assertIsNot(id_clause.left, Notification.__table__.c.id)",
    "self.assertIs(id_clause.left, Other.columns.id)",
    "self.assertIs(id_clause.left, Notification.id)",
    "self.assertTrue('id_clause.left is Notification.__table__.c.id' in text)",
    "assert status == 200, 'id_clause.left is Notification.__table__.c.id'",
])
def test_meaningful_and_nonmatching_assertions_are_unchanged(source):
    assert gate.candidate_test_assertion_findings(worker("def test_sql(self):\n    " + source + "\n")) == []


@pytest.mark.parametrize("path,original", [
    ("tests/test_unrelated.py", None),
    (PATH, "b" * 64),
    ("api/routers/notifications.py", None),
    ("tests/test_p1_00b_characterization_bad-name.py", None),
])
def test_scope_remains_new_canonical_characterization_only(path, original):
    source = "assert clause.left is Notification.__table__.c.id\n"
    assert gate.candidate_test_assertion_findings(worker(source, path, original)) == []


def test_p1_and_p1b_patterns_bounded_order_detached_and_syntax_owned_elsewhere():
    source = "assert c.left is Notification.__table__.c.id\n"
    changes = [{"path": f"tests/test_p1_00_characterization_{i}.py",
                "original_sha256": None, "content": source} for i in range(40)]
    candidate = {"changes": changes}
    before = copy.deepcopy(candidate)
    findings = gate.candidate_test_assertion_findings(candidate)
    assert len(findings) == 32
    assert findings[0].startswith("tests/test_p1_00_characterization_0.py:1:")
    assert findings[-1].startswith("tests/test_p1_00_characterization_31.py:1:")
    findings.clear()
    assert len(gate.candidate_test_assertion_findings(candidate)) == 32
    assert candidate == before
    assert gate.candidate_test_assertion_findings(worker("def broken(")) == []


def test_same_line_duplicates_collapse_and_existing_two_site_rules_stay_active():
    source = "assert a.left is M.__table__.c.id and b.left is M.__table__.c.user_id\n"
    assert len(gate.candidate_test_assertion_findings(worker(source))) == 1
    candidate = worker("def test_a():\n    assert True if True else False\n",
                       "tests/test_two_site_example.py", "b" * 64)
    assert gate.candidate_test_assertion_findings(candidate) == [
        "tests/test_two_site_example.py:2: constant-branch"
    ]


class StopAtWorker(Exception):
    pass


def run_remainder(root, candidate, count, seen):
    return pipeline._run_post_worker_remainder(
        root=root, task_candidate=root / "task.json", task={}, sources={},
        initial_head="a" * 40, initial_refs={}, run_prefix="sql-identity",
        timeout=300, cli=None, state={}, evidence_dir=root,
        state_check=lambda: None, worker_dir=root / "worker", worker_res=candidate,
        current_worker=("sql-identity-worker-0", SHA, "source", "original-session"),
        seen_convs={"original-session"}, correction_count=count,
        seen_norm_findings=set(), seen_test_signatures=set(),
        seen_bootstrap_signatures=seen,
    )


def test_real_remainder_returns_sha_bound_defect_before_sandbox_or_reviewer(tmp_path, monkeypatch):
    candidate = worker("self.assertIs(id_clause.left, Notification.__table__.c.id)\n")
    before = copy.deepcopy(candidate)
    monkeypatch.setattr(pipeline, "candidate_test_assertion_findings",
                        gate.candidate_test_assertion_findings)
    sandbox = Mock(side_effect=AssertionError("sandbox must not run"))
    provider = Mock(side_effect=StopAtWorker)
    monkeypatch.setattr(pipeline, "persist_pre_review_tests", sandbox)
    monkeypatch.setattr(pipeline, "run_provider_session", provider)
    with pytest.raises(StopAtWorker):
        run_remainder(tmp_path, candidate, 0, set())
    sandbox.assert_not_called()
    assert provider.call_count == 1
    call = provider.call_args
    assert call.args[2:4] == ("worker", "sql-identity-worker-1")
    assert call.kwargs["expected_head"] == "a" * 40
    assert call.kwargs["proposal"] == tmp_path / "worker/result.json"
    feedback = json.loads((tmp_path / "feedback-correction-1.json").read_text())
    assert feedback["reviewer"] == "test-gate"
    assert feedback["reviewed_proposal_sha256"] == SHA
    assert feedback["findings"] == [
        PATH + ":1: sqlalchemy-column-object-identity; use exact compiled SQL "
        "and column-specific parameter mapping, not Python identity of wrapped columns."
    ]
    assert feedback["test_evidence"] == [
        "gate=two-site-test-assertions", "proposal_sha256=" + SHA, "finding_count=1"
    ]
    assert candidate == before


@pytest.mark.parametrize("kind", ["exhausted", "repeated"])
def test_real_remainder_keeps_correction_budget_and_repetition_gate(tmp_path, monkeypatch, kind):
    candidate = worker("assert clause.left is Notification.__table__.c.id\n")
    monkeypatch.setattr(pipeline, "candidate_test_assertion_findings",
                        gate.candidate_test_assertion_findings)
    sandbox = Mock(side_effect=AssertionError("sandbox must not run"))
    provider = Mock(side_effect=AssertionError("provider must not run"))
    monkeypatch.setattr(pipeline, "persist_pre_review_tests", sandbox)
    monkeypatch.setattr(pipeline, "run_provider_session", provider)
    findings = gate.candidate_test_assertion_findings(candidate)
    signature = hashlib.sha256(json.dumps(
        findings, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    seen = {signature} if kind == "repeated" else set()
    message = "repeated test bootstrap failure" if kind == "repeated" else "correction exhaustion"
    with pytest.raises(pipeline.ProposalError, match=message):
        run_remainder(tmp_path, candidate, 0 if kind == "repeated" else 2, seen)
    sandbox.assert_not_called()
    provider.assert_not_called()
    assert not list(tmp_path.glob("feedback-correction-*"))


@pytest.mark.parametrize("profile_position", [0, 1, 3])
def test_machine_profile_counts_as_physical_success_coverage_requirement(profile_position):
    from scripts.two_site_task_context import preparation_notes
    from tests.test_two_site_byte_factor_prevention import notes_fixture
    task, sources, _ = notes_fixture()
    profile = task["requirements"][0]
    task["requirements"] = ["R1: first", "R2: second", "R3: third"]
    task["requirements"].insert(profile_position, profile)
    before = copy.deepcopy((task, sources))
    result = preparation_notes(task, sources)
    assert result["output_contract"]["success_covered_requirements"] == [1, 2, 3, 4]
    result["output_contract"]["success_covered_requirements"].clear()
    assert preparation_notes(task, sources)["output_contract"]["success_covered_requirements"] == [1, 2, 3, 4]
    assert (task, sources) == before


def test_all_32_coverage_ids_survive_advisory_trimming_and_legacy_unchanged():
    from scripts.two_site_task_context import preparation_notes
    from tests.test_two_site_byte_factor_prevention import notes_fixture
    task, sources, profile = notes_fixture(
        {f"tests/test_p1_00b_characterization_{'x' * 60}{i:02}.py": i + 1 for i in range(32)},
        total=160000,
    )
    task["requirements"] = ["legacy narrative"] * 31 + task["requirements"]
    task["goal"] = " ".join(f"DR-{i}" for i in range(40))
    before = copy.deepcopy((task, sources))
    result = preparation_notes(task, sources)
    assert len(json.dumps(result).encode("utf-8")) <= 8192
    assert result["output_contract"]["proposal_utf8_budget_profile"] == profile
    assert result["output_contract"]["success_covered_requirements"] == list(range(1, 33))
    assert (task, sources) == before
    task["requirements"] = ["legacy"]
    assert "success_covered_requirements" not in preparation_notes(task, sources)["output_contract"]
