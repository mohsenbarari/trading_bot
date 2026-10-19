"""Retained association, real pytest binding and partial Root receipt regressions."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import ast
import copy
import json

import pytest

from scripts import two_site_accepted_work_proofs as subject
from scripts import two_site_accepted_work_snapshot as custody
from tests.test_two_site_accepted_work_attestation import (
    ProofRepository, METRICS, PRIVATE, raw, canonical, digest, fixed,
)
from tests.test_two_site_accepted_work_proofs import (
    collect, expected_output, expected_refs, assert_unknown, rewrite_chain,
)


def metric_ref(repo):
    return {"path": METRICS, "sha256": digest(repo.artifacts[METRICS]), "pointer": ""}


@pytest.mark.parametrize("field", ["task_id", "task_sha256", "head_before", "gap_reference"])
@pytest.mark.parametrize("missing", ["absent", "null"])
def test_later_incomplete_association_is_unbound_not_conflicting_credit(tmp_path, monkeypatch, field, missing):
    repo = ProofRepository(tmp_path / "repo")
    later = copy.deepcopy(repo.metric)
    later.update(run_id="later-incomplete", state="ERROR")
    if missing == "absent":
        later.pop(field)
    else:
        later[field] = None
    repo.artifact(METRICS, [repo.metric, later])
    value, observed = collect(repo, monkeypatch)
    expected = expected_output(repo, observed)
    expected["run_bindings"].append({"run_id": "later-incomplete", "metrics_ordinals": [1],
        "semantic_fingerprint": None, "work_fingerprint": None, "state": "UNBOUND"})
    expected["unknown"] = [{"work_fingerprint": None, "semantic_fingerprint": None,
        "reason_code": "UNBOUND_METRICS", "proof_refs": [metric_ref(repo)],
        "retained_run_ids": ["later-incomplete"], "metrics_ordinals": [1], "index_ordinals": []}]
    expected["retention_limits"].update(metrics_record_count=2,
        limitations=sorted([*expected["retention_limits"]["limitations"], "UNKNOWN_RETAINED_ROWS"]))
    assert value == expected
    assert sorted(i for row in value["run_bindings"] for i in row["metrics_ordinals"]) == [0, 1]


@pytest.mark.parametrize("field,value", [("task_id", "../unsafe"), ("task_sha256", "0" * 64),
    ("head_before", "0" * 40), ("gap_reference", "docs/other.json#wrong")])
def test_present_invalid_association_quarantines_connected_work(tmp_path, monkeypatch, field, value):
    repo = ProofRepository(tmp_path / "repo")
    later = copy.deepcopy(repo.metric)
    later.update(run_id="later-invalid", state="ERROR")
    later[field] = value
    repo.artifact(METRICS, [repo.metric, later])
    result, _ = collect(repo, monkeypatch)
    assert_unknown(result, "IDENTITY_CONFLICT")
    assert [row["state"] for row in result["run_bindings"]] == ["CONFLICT", "CONFLICT"]
    assert sorted(i for row in result["run_bindings"] for i in row["metrics_ordinals"]) == [0, 1]


@pytest.mark.parametrize("variant", ["same-id", "invalid-with-missing"])
def test_missing_fields_do_not_hide_same_run_variants_or_present_invalid_fields(tmp_path, monkeypatch, variant):
    repo = ProofRepository(tmp_path / "repo")
    valid, incomplete = copy.deepcopy(repo.metric), copy.deepcopy(repo.metric)
    valid.update(run_id="later-variant", state="ERROR")
    incomplete.update(run_id="later-variant", state="ERROR", task_id=None)
    if variant == "same-id":
        rows = [repo.metric, valid, incomplete]
    else:
        incomplete["task_sha256"] = "malformed-present-hash"
        rows = [repo.metric, incomplete]
    repo.artifact(METRICS, rows)
    result, _ = collect(repo, monkeypatch)
    assert_unknown(result, "IDENTITY_CONFLICT")
    assert all(row["state"] == "CONFLICT" for row in result["run_bindings"])
    assert sorted(i for row in result["run_bindings"] for i in row["metrics_ordinals"]) == list(range(len(rows)))


class ShadowRepository(ProofRepository):
    """Real Git/proposal AFTERs differ only at the selected pytest binding."""

    def __init__(self, root, defect):
        self.defect = defect
        super().__init__(root)
        if defect == "non-test":
            self.selectors[0] = self.accepted_path + "::TestLiteral::helper_buy"
            rewrite_chain(self)

    def put(self, path, body):
        if path == "tests/test_retained_proof.py":
            if self.defect == "overwrite":
                body = body.replace(b"    def test_sell", b"    test_buy = None\n\n    def test_sell")
            elif self.defect == "conditional":
                body = body.replace(b"    def test_sell", b"    if True:\n        test_buy = None\n\n    def test_sell")
            else:
                body = body.replace(b"def test_buy", b"def helper_buy")
            self.after = body
        return super().put(path, body)


@pytest.mark.parametrize("defect", ["overwrite", "conditional", "non-test"])
def test_real_accepted_test_selector_must_resolve_unshadowed_pytest_leaf(tmp_path, monkeypatch, defect):
    repo = ShadowRepository(tmp_path / "repo", defect)
    assert repo.run("show", repo.accepted + ":" + repo.accepted_path) == repo.after
    assert repo.proposal["changes"][0]["content"].encode() == repo.after
    result, _ = collect(repo, monkeypatch)
    # One contradictory selector quarantines its whole connected accepted work.
    assert result["works"] == [] and result["handler_scenarios"] == []
    excluded = {repo.worker_dir + "status.json", repo.native_path,
                *(repo.reviewer_dir + leaf for leaf in ("task.json", "status.json", "result.json")),
                *(repo.root_dir + leaf for leaf in ("status.json", "result.json"))}
    refs = expected_refs(repo)
    early = [ref for ref in refs if ref["path"] not in excluded and
             not ref["pointer"].startswith("/registered_handlers/1")]
    later = [ref for ref in refs if not ref["pointer"].startswith("/registered_handlers/0")]
    binding_paths = set(repo.source) | {repo.task_path, METRICS}
    binding_refs = [ref for ref in refs if ref["pointer"] == "" and ref["path"] in binding_paths]
    common = {"work_fingerprint": repo.work, "semantic_fingerprint": repo.identity["fingerprint"],
              "retained_run_ids": [repo.controller], "metrics_ordinals": [0]}
    expected_unknown = [
        {**common, "reason_code": "SOURCE_MISMATCH", "proof_refs": early, "index_ordinals": [0]},
        {**common, "reason_code": "IDENTITY_CONFLICT", "proof_refs": later, "index_ordinals": [1]},
        {**common, "reason_code": "IDENTITY_CONFLICT", "proof_refs": binding_refs, "index_ordinals": []}]
    assert result["unknown"] == sorted(expected_unknown, key=canonical)
    assert result["run_bindings"] == [{"run_id": repo.controller, "metrics_ordinals": [0],
        "semantic_fingerprint": repo.identity["fingerprint"], "work_fingerprint": repo.work, "state": "CONFLICT"}]
    assert result["retention_limits"]["proved_index_ordinals"] == []
    assert result["retention_limits"]["unresolved_index_ordinals"] == [0, 1]


@pytest.mark.parametrize("field,value", [("source_sha256", "0" * 64), ("scenario_key", "different-scenario"),
                                        ("stage_id", "P2")])
@pytest.mark.parametrize("null_run", [False, True])
def test_known_fingerprint_projection_conflict_precedes_missing_association(tmp_path, monkeypatch, field, value, null_run):
    repo = ProofRepository(tmp_path / "repo")
    later = copy.deepcopy(repo.metric)
    later.update(run_id=None if null_run else "later-projection", state="ERROR", task_id=None)
    later["semantic_identity"][field] = value
    repo.artifact(METRICS, [repo.metric, later])
    result, _ = collect(repo, monkeypatch)
    assert_unknown(result, "IDENTITY_CONFLICT")
    assert all(row["state"] == "CONFLICT" for row in result["run_bindings"])
    assert result["run_bindings"][1]["run_id"] == later["run_id"]
    assert sorted(i for row in result["run_bindings"] for i in row["metrics_ordinals"]) == [0, 1]


class RecursiveSourceRepository(ProofRepository):
    def put(self, path, body):
        if path == "src/helper0.py":
            body = b"import math\n\ndef helper_0(value):\n    return " + b"1+" * 10000 + b"1\n"
            self.source[path] = body
        return super().put(path, body)


def test_real_bounded_source_ast_recursion_is_fatal_count_not_unknown(tmp_path):
    repo = RecursiveSourceRepository(tmp_path / "repo")
    assert len(repo.source["src/helper0.py"]) < 24000
    with pytest.raises(RecursionError):
        ast.parse(repo.source["src/helper0.py"])
    with pytest.raises(custody._EvidenceSnapshotError) as final:
        with custody._proof_snapshot(repo.root) as snapshot:
            fixed("COUNT_LIMIT", lambda: subject._collect_accepted_work_proofs(snapshot))
            with pytest.raises(custody._EvidenceSnapshotError) as closed:
                snapshot._metadata()
            assert closed.value.code == "CLOSED_SNAPSHOT"
    assert final.value.code == "COUNT_LIMIT"


@pytest.mark.parametrize("field,value,reason", [("summary", False, "ACCEPTANCE_MISMATCH"),
    ("summary", " ", "ACCEPTANCE_MISMATCH"), ("next_action", [], "ACCEPTANCE_MISMATCH"),
    ("next_action", None, "ACCEPTANCE_MISMATCH"), ("schema_version", "2.0", "UNSUPPORTED_SCHEMA")])
def test_present_partial_root_status_is_validated_even_without_result(tmp_path, monkeypatch, field, value, reason):
    repo = ProofRepository(tmp_path / "repo", root_receipts=False)
    repo.root_status[field] = value
    repo.artifact(repo.root_dir + "status.json", repo.root_status)
    repo.publish()  # Actor now attests the actual malformed raw status SHA.
    assert not (repo.root / (repo.root_dir + "result.json")).exists()
    result, _ = collect(repo, monkeypatch)
    assert_unknown(result, reason)
    assert result["works"] == []  # No downgrade to HISTORICAL despite missing result.


def test_supported_partial_root_status_keeps_honest_historical_tier(tmp_path, monkeypatch):
    repo = ProofRepository(tmp_path / "repo", root_receipts=False)
    repo.root_status.update(schema_version="1.0", summary="Historical Source receipt", next_action="Continue")
    repo.artifact(repo.root_dir + "status.json", repo.root_status)
    repo.publish()
    result, observed = collect(repo, monkeypatch)
    assert result == expected_output(repo, observed)
    assert result["works"][0]["evidence_level"] == "HISTORICAL_ATTESTED_CORROBORATED"


def test_all_raw_ordinals_alias_corrections_and_duplicate_cost_rows_are_preserved(tmp_path, monkeypatch):
    repo = ProofRepository(tmp_path / "repo")
    alias_task = copy.deepcopy(repo.task)
    alias_task["task_id"] = "retained-task-alias"
    alias_path = ".local/two-site-refactor/tasks/retained-task-alias.json"
    repo.artifact(alias_path, alias_task)
    alias = copy.deepcopy(repo.metric)
    alias.update(run_id="correction-run", task_id=alias_task["task_id"], state="ERROR",
                 task_sha256=digest(json.dumps(alias_task, sort_keys=True).encode()), head_after=repo.parent)
    duplicate = copy.deepcopy(repo.metric)
    duplicate["total_tokens"] = 12345  # Cost differences do not manufacture distinct work.
    malformed = [None, [], True, {"run_id": "../" + PRIVATE},
                 {"run_id": "bad-shape", "work_lineage": {"fingerprint": []}},
                 {"run_id": "bad-semantic", "semantic_identity": {"fingerprint": {}}}]
    metrics = [repo.metric, duplicate, alias, *malformed]
    repo.metrics = metrics
    repo.artifact(METRICS, metrics)
    value, observed = collect(repo, monkeypatch)
    expected = expected_output(repo, observed)
    expected["snapshot"]["files"] = sorted(expected["snapshot"]["files"], key=lambda r: r["path"])
    expected["works"][0]["retained_run_ids"] = ["correction-run", repo.controller]
    expected["works"][0]["proof_refs"].append({"path": alias_path, "sha256": digest(raw(alias_task)), "pointer": ""})
    expected["works"][0]["proof_refs"].sort(key=lambda r: (r["path"], r["sha256"], r["pointer"]))
    expected["run_bindings"] = [
        {"run_id": repo.controller, "metrics_ordinals": [0, 1], "semantic_fingerprint": repo.identity["fingerprint"],
         "work_fingerprint": repo.work, "state": "BOUND"},
        {"run_id": "correction-run", "metrics_ordinals": [2], "semantic_fingerprint": repo.identity["fingerprint"],
         "work_fingerprint": repo.work, "state": "BOUND"},
        *[{"run_id": run, "metrics_ordinals": [ordinal], "semantic_fingerprint": None,
           "work_fingerprint": None, "state": "UNBOUND"}
          for ordinal, run in ((3, None), (4, None), (5, None), (6, None), (7, "bad-shape"), (8, "bad-semantic"))]]
    expected["unknown"] = sorted([{"work_fingerprint": None, "semantic_fingerprint": None,
        "reason_code": "UNBOUND_METRICS", "proof_refs": [metric_ref(repo)], "retained_run_ids": [run] if run else [],
        "metrics_ordinals": [ordinal], "index_ordinals": []}
        for ordinal, run in ((3, None), (4, None), (5, None), (6, None), (7, "bad-shape"), (8, "bad-semantic"))],
        key=lambda item: canonical(item))
    expected["retention_limits"].update(metrics_record_count=9,
        limitations=sorted([*expected["retention_limits"]["limitations"], "UNKNOWN_RETAINED_ROWS"]))
    assert value == expected
    assert sorted(i for binding in value["run_bindings"] for i in binding["metrics_ordinals"]) == list(range(9))
    assert PRIVATE not in json.dumps(value, ensure_ascii=False)
    # Permutation changes ordinals/hash, not semantic work or handler denominator.
    repo.artifact(METRICS, list(reversed(metrics)))
    reordered, _ = collect(repo, monkeypatch)
    for field in ("work_fingerprint", "semantic_fingerprints", "source_revisions", "evidence_level", "retained_run_ids"):
        assert reordered["works"][0][field] == value["works"][0][field]
    assert reordered["handler_scenarios"] == value["handler_scenarios"]
    assert sorted(i for binding in reordered["run_bindings"] for i in binding["metrics_ordinals"]) == list(range(9))


def test_connected_run_identity_conflict_quarantines_all_handler_proofs(tmp_path, monkeypatch):
    repo = ProofRepository(tmp_path / "repo")
    a, b = copy.deepcopy(repo.metric), copy.deepcopy(repo.metric)
    a.update(run_id="connected-conflict", state="ERROR")
    b.update(run_id="connected-conflict", state="ERROR", head_after=repo.parent)
    repo.artifact(METRICS, [repo.metric, a, b])
    value, _ = collect(repo, monkeypatch)
    assert_unknown(value, "IDENTITY_CONFLICT")
    assert value["run_bindings"] == [
        {"run_id": repo.controller, "metrics_ordinals": [0], "semantic_fingerprint": repo.identity["fingerprint"],
         "work_fingerprint": repo.work, "state": "CONFLICT"},
        {"run_id": "connected-conflict", "metrics_ordinals": [1, 2], "semantic_fingerprint": None,
         "work_fingerprint": None, "state": "CONFLICT"}]
    assert "UNKNOWN_RETAINED_ROWS" in value["retention_limits"]["limitations"]
