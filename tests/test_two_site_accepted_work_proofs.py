"""Whole retained-proof joins and independent, adversarial real-Git witnesses."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import copy
import json

import pytest

from scripts import two_site_accepted_work_attestation as attest
from scripts import two_site_accepted_work_proofs as subject
from scripts import two_site_accepted_work_snapshot as custody
from tests.test_two_site_accepted_work_attestation import (
    ProofRepository, LEDGER, INVENTORY, GAP, GAP_ID, GAP_PATH, METRICS,
    FIXED_LIMITS, raw, canonical, digest, blob_oid, read_counter,
)


def expected_refs(repo):
    """Literal semantic reference set, independently derived from fixture declarations."""
    blobs = dict(repo.source)
    blobs.update({repo.accepted_path: repo.after, LEDGER: repo.ledger,
                  repo.receipt_path: raw(repo.receipt)})
    if repo.registration is not None:
        blobs[repo.registration_path] = raw(repo.registration)
    if repo.custody_doc is not None:
        blobs[repo.custody_path] = raw(repo.custody_doc)
    chain_paths = [repo.task_path, repo.native_path, METRICS]
    chain_paths += [directory + name for directory in (repo.worker_dir, repo.reviewer_dir)
                    for name in ("task.json", "status.json", "result.json")]
    chain_paths += [repo.root_dir + name for name in ("status.json", "result.json")
                    if repo.root_dir + name in repo.artifacts]
    refs = {(path, digest(body), "") for path, body in blobs.items()}
    refs.update((path, digest(repo.artifacts[path]), "") for path in chain_paths)
    receipt_sha = digest(raw(repo.receipt))
    refs.update((repo.receipt_path, receipt_sha, pointer)
                for pointer in ("/source", "/actor", "/scope", "/git"))
    for index in range(2):
        refs.add((repo.receipt_path, receipt_sha, f"/registered_handlers/{index}"))
        path = repo.receipt_path if repo.form != "recovery" else repo.registration_path
        document = repo.receipt if repo.form != "recovery" else repo.registration
        refs.add((path, digest(raw(document)), f"/registered_handlers/{index}/evidence_limits"))
    if repo.form == "recovery":
        refs.update((repo.registration_path, digest(raw(repo.registration)), pointer)
                    for pointer in ("/source", "/actor", "/scope", "/git"))
    if repo.form == "login":
        refs.update((repo.registration_path, digest(raw(repo.registration)), pointer) for pointer in
            ("/actor", "/actor/worker", "/actor/reviewer", "/source/source_proposal_sha256",
             "/source/source_manifest_sha256", "/scope/source_assignment", "/scope/registration_assignment"))
    return [{"path": path, "sha256": sha, "pointer": pointer} for path, sha, pointer in sorted(refs)]


def expected_snapshot(repo, observed):
    blobs = dict(repo.source)
    blobs.update({repo.accepted_path: repo.after, LEDGER: repo.ledger,
                  repo.receipt_path: raw(repo.receipt)})
    commits = {repo.parent, repo.accepted, repo.head}
    if repo.registration is not None:
        blobs[repo.registration_path] = raw(repo.registration)
        commits.add(repo.registration_commit)
    if repo.custody_doc is not None:
        blobs[repo.custody_path] = raw(repo.custody_doc)
        commits.add(repo.custody_commit)
    objects = {blob_oid(body): body for body in blobs.values()}
    objects.update({commit: repo.commit_bodies[commit] for commit in commits})
    files = {**repo.artifacts, LEDGER: repo.ledger, repo.receipt_path: raw(repo.receipt)}
    return {"schema_version": 1, "head": repo.head, "protected_refs": repo.refs,
            "files": [{"path": path, "sha256": digest(body), "size": len(body)}
                      for path, body in sorted(files.items())],
            "git_objects": [{"git_oid": oid, "sha256": digest(body), "size": len(body)}
                            for oid, body in sorted(objects.items())],
            "bytes_read": sum(len(item[2]) for item in observed)}


def expected_output(repo, observed):
    tier = "ORIGINAL_ROOT_RECEIPT_CORROBORATED" if repo.root_receipts else "HISTORICAL_ATTESTED_CORROBORATED"
    limits = list(FIXED_LIMITS)
    if repo.form != "inline":
        limits.append("ORIGINAL_ROOT_NOT_DECLARED")
    elif not repo.root_receipts:
        limits.append("MISSING_ORIGINAL_ROOT_RECEIPTS")
    return {"schema_version": 1, "state": "OBSERVATIONAL_PROOF_JOIN",
        "snapshot": expected_snapshot(repo, observed),
        "works": [{"work_fingerprint": repo.work,
            "semantic_fingerprints": [repo.identity["fingerprint"]],
            "source_revisions": [{"semantic_fingerprint": repo.identity["fingerprint"],
                "source_sha256": repo.identity["source_sha256"], "source_head": repo.parent,
                "accepted_source_commit": repo.accepted}], "evidence_level": tier,
            "proof_refs": expected_refs(repo), "retained_run_ids": [repo.controller]}],
        "handler_scenarios": [{"work_fingerprint": repo.work, "gap_reference": GAP,
            "canonical_symbol": "src/market.py:handle_" + name, "scenario_key": name + "-literal",
            "witnesses": [{"selector": repo.selectors[index], "source_commit": repo.accepted,
                           "source_file_sha256": digest(repo.after)}]}
            for index, name in enumerate(("buy", "sell"))],
        "run_bindings": [{"run_id": repo.controller, "metrics_ordinals": [0],
            "semantic_fingerprint": repo.identity["fingerprint"], "work_fingerprint": repo.work, "state": "BOUND"}],
        "unknown": [], "retention_limits": {"index_row_count": 2, "metrics_record_count": 1,
            "proved_index_ordinals": [0, 1], "unresolved_index_ordinals": [], "limitations": sorted(limits)}}


def whole_oracle(repo, value, observed):
    """The SAME full oracle is used before corruption, after corruption and restoration."""
    assert value == expected_output(repo, observed)
    assert set(value) == {"schema_version", "state", "snapshot", "works", "handler_scenarios",
                          "run_bindings", "unknown", "retention_limits"}
    assert json.loads(json.dumps(value, allow_nan=False)) == value


def collect(repo, monkeypatch):
    with read_counter(monkeypatch) as observed:
        with custody._proof_snapshot(repo.root) as snapshot:
            value = subject._collect_accepted_work_proofs(snapshot)
            observed_at_return = list(observed)
    return value, observed_at_return


@pytest.mark.parametrize("form,linked", [("inline", False), ("recovery", False), ("login", True)])
def test_three_actual_forms_whole_literal_oracle_and_same_corruption(tmp_path, monkeypatch, form, linked):
    repo = ProofRepository(tmp_path / "repo", form=form, linked=linked)
    assert len(repo.sources) == 9 and len(repo.identity["source_manifest"]) == 15
    assert digest(raw(repo.task)) != digest(raw(repo.worker_snapshot))
    assert digest(raw(repo.task)) != repo.metric["task_sha256"]
    assert repo.metric["task_sha256"] != digest(raw(repo.worker_snapshot))
    value, observed = collect(repo, monkeypatch)
    whole_oracle(repo, value, observed)
    original = copy.deepcopy(value)
    value["works"][0]["source_revisions"][0]["source_head"] = "0" * 40
    with pytest.raises(AssertionError):
        whole_oracle(repo, value, observed)
    value = original
    whole_oracle(repo, value, observed)
    # Returned builtins are detached from both the live owner and a later join.
    value["works"][0]["proof_refs"].clear()
    later, later_reads = collect(repo, monkeypatch)
    whole_oracle(repo, later, later_reads)
    assert later["handler_scenarios"][0]["scenario_key"] != repo.identity["scenario_key"]
    assert len(later["works"]) == 1 and len(later["handler_scenarios"]) == 2


def rewrite_chain(repo):
    """Rebind every declared RAW domain after a fault, so its semantic guard is reached."""
    repo.proposal_bytes = raw(repo.proposal)
    proposal_hash = digest(repo.proposal_bytes)
    repo.worker_status["result_sha256"] = proposal_hash
    repo.native["proposal_sha256"] = proposal_hash
    repo.reviewer_status.update(result_sha256=digest(raw(repo.review)),
        input_proposal_sha256=proposal_hash, input_test_evidence_sha256=digest(raw(repo.native)))
    repo.reviewer_snapshot.update(task=copy.deepcopy(repo.worker_snapshot["task"]),
        source_head=repo.worker_snapshot["source_head"], sources=copy.deepcopy(repo.worker_snapshot["sources"]),
        proposal=repo.proposal_bytes.decode(), test_evidence=copy.deepcopy(repo.native))
    repo.root_result["proposal_sha256"] = proposal_hash
    repo.root_status["proposal_sha256"] = proposal_hash
    repo.write_retained()
    repo.publish()


def assert_unknown(value, reason):
    assert value["works"] == [] and value["handler_scenarios"] == []
    rows = [item for item in value["unknown"] if item["index_ordinals"]]
    assert rows and {item["reason_code"] for item in rows} == {reason}
    assert sorted(index for item in rows for index in item["index_ordinals"]) == [0, 1]
    assert value["retention_limits"]["proved_index_ordinals"] == []
    assert value["retention_limits"]["unresolved_index_ordinals"] == [0, 1]
    assert "INCOMPLETE_ACCEPTANCE" in value["retention_limits"]["limitations"]
    for item in rows:
        assert set(item) == {"work_fingerprint", "semantic_fingerprint", "reason_code", "proof_refs",
                            "retained_run_ids", "metrics_ordinals", "index_ordinals"}
        assert item["proof_refs"] == sorted(item["proof_refs"], key=lambda r: (r["path"], r["sha256"], r["pointer"]))


@pytest.mark.parametrize("case,reason", [
    ("snapshot-content", "SOURCE_MISMATCH"), ("snapshot-extra-source", "SOURCE_MISMATCH"),
    ("source-head", "SOURCE_MISMATCH"), ("excerpt-text", "SOURCE_MISMATCH"),
    ("excerpt-missing-file", "SOURCE_MISMATCH"), ("excerpt-context-overlap", "SOURCE_MISMATCH"),
    ("excerpt-import", "SOURCE_MISMATCH"), ("excerpt-decorator", "SOURCE_MISMATCH"),
    ("excerpt-bool-start", "SOURCE_MISMATCH"), ("excerpt-float-end", "SOURCE_MISMATCH"),
    ("excerpt-unicode-lines", "SOURCE_MISMATCH"), ("new-null-context", "SOURCE_MISMATCH"),
    ("proposal-content", "PROPOSAL_MISMATCH"), ("proposal-extra-key", "PROPOSAL_MISMATCH"),
    ("proposal-duplicate", "PROPOSAL_MISMATCH"), ("proposal-before", "PROPOSAL_MISMATCH"),
    ("native-return-bool", "NATIVE_MISMATCH"), ("native-isolation", "NATIVE_MISMATCH"),
    ("native-selector", "NATIVE_MISMATCH"), ("native-pending", "NATIVE_MISMATCH"),
    ("review-role", "REVIEWER_MISMATCH"), ("review-conversation", "REVIEWER_MISMATCH"),
    ("review-input-proposal", "REVIEWER_MISMATCH"), ("review-input-native", "REVIEWER_MISMATCH"),
    ("review-input-worker", "REVIEWER_MISMATCH"), ("review-input-conversation", "REVIEWER_MISMATCH"),
    ("review-runtime", "REVIEWER_MISMATCH"), ("review-snapshot", "REVIEWER_MISMATCH"),
    ("root-state", "ACCEPTANCE_MISMATCH"), ("root-status", "ACCEPTANCE_MISMATCH"),
])
def test_complete_chain_typed_guards_reached_after_rebinding(tmp_path, monkeypatch, case, reason):
    repo = ProofRepository(tmp_path / "repo")
    envelope = json.loads(repo.task["goal"].split("CANONICAL_SOURCE_EXCERPTS_JSON:\n")[1])
    if case == "snapshot-content":
        repo.worker_snapshot["sources"][repo.context[0]]["content"] = "forged = True\n"
    elif case == "snapshot-extra-source":
        repo.worker_snapshot["sources"]["src/unlisted.py"] = {"content": "x=1", "sha256": "1" * 64}
    elif case == "source-head":
        repo.worker_snapshot["source_head"] = repo.accepted
    elif case.startswith("excerpt-"):
        excerpt = next(item for item in envelope["excerpts"] if item["path"] == "src/market.py")
        if case == "excerpt-missing-file":
            envelope["excerpts"].pop()
        elif case == "excerpt-context-overlap":
            repo.task["context_files"].append("src/market.py")
            repo.worker_snapshot["sources"]["src/market.py"] = {
                "content": repo.source["src/market.py"].decode(), "sha256": digest(repo.source["src/market.py"])}
        elif case == "excerpt-text":
            excerpt["ranges"][1]["text"] += "# forged\n"
        elif case == "excerpt-import":
            excerpt["ranges"].pop(0)
        elif case == "excerpt-decorator":
            excerpt["ranges"][1]["start"] += 1
            excerpt["ranges"][1]["text"] = excerpt["ranges"][1]["text"].split("\r\n", 1)[1]
        elif case == "excerpt-bool-start":
            excerpt["ranges"][0]["start"] = True
        elif case == "excerpt-float-end":
            excerpt["ranges"][0]["end"] = 1.0
        else:
            excerpt = next(item for item in envelope["excerpts"] if item["path"] == "src/helper3.py")
            excerpt["ranges"][-1]["end"] += 1
        repo.task["goal"] = repo.task["goal"].split("CANONICAL_SOURCE_EXCERPTS_JSON:\n")[0] + \
            "CANONICAL_SOURCE_EXCERPTS_JSON:\n" + canonical(envelope).decode()
    elif case == "new-null-context":
        repo.worker_snapshot["sources"][repo.context[0]] = {"content": None, "sha256": None}
    elif case == "proposal-content":
        repo.proposal["changes"][0]["content"] += "# not in accepted Git\n"
    elif case == "proposal-extra-key":
        repo.proposal["changes"][0]["extra"] = True
    elif case == "proposal-duplicate":
        repo.proposal["changes"].append(copy.deepcopy(repo.proposal["changes"][0]))
    elif case == "proposal-before":
        repo.proposal["changes"][0]["original_sha256"] = "1" * 64
    elif case.startswith("native-"):
        field, value = {"native-return-bool": ("returncode", False),
            "native-isolation": ("isolation", "host"), "native-selector": ("selectors", []),
            "native-pending": ("state", "PENDING")}[case]
        repo.native[field] = value
    elif case == "review-role":
        repo.reviewer_status["role"] = "worker"
    elif case == "review-conversation":
        repo.reviewer_status["conversation_id"] = "worker-conversation"
    elif case == "review-runtime":
        repo.reviewer_status["runtime_profile"] = dict(
            conversation_id="wrong", actual_executor_checked=True, name="x", command_policy="off", native_tools=0)
    elif case == "root-state":
        repo.root_result["state"] = "BLOCKED"
    elif case == "root-status":
        repo.root_status["head_after"] = repo.parent
    rewrite_chain(repo)
    if case.startswith("review-input-"):
        field = {"review-input-proposal": "input_proposal_sha256", "review-input-native": "input_test_evidence_sha256",
                 "review-input-worker": "input_worker_run_id", "review-input-conversation": "input_worker_conversation_id"}[case]
        repo.reviewer_status[field] = "1" * 64
        repo.artifact(repo.reviewer_dir + "status.json", repo.reviewer_status)
    if case == "review-snapshot":
        repo.reviewer_snapshot["proposal"] += " "
        repo.artifact(repo.reviewer_dir + "task.json", repo.reviewer_snapshot)
    value, _ = collect(repo, monkeypatch)
    assert_unknown(value, reason)


def revision(repo, markdown=False, gap_fault=None):
    """Create a second real Git parent/accepted pair, without mocked proof."""
    other = copy.deepcopy(repo)
    original_receipt, original_rows = raw(repo.receipt), copy.deepcopy(repo.rows)
    (other.root / other.accepted_path).unlink()
    other.source[other.context[0]] = b"constant_0 = 99\n"
    other.put(other.context[0], other.source[other.context[0]])
    parent_paths = [other.context[0], other.accepted_path]
    gap = GAP
    if gap_fault:
        document = copy.deepcopy(other.gap)
        item = document["blocker_registry"]["items"][0]
        if gap_fault == "duplicate":
            document["blocker_registry"]["items"].append(copy.deepcopy(item))
        else:
            item[gap_fault] = "not-authority"
        other.source[GAP_PATH] = raw(document)
        other.put(GAP_PATH, other.source[GAP_PATH])
        parent_paths.append(GAP_PATH)
    if markdown:
        path = "docs/refactor/two-server/section-1/old-ledger.md"
        body = ("# Generic historical registry locator\n" + (GAP_ID + " mention only\n") * 42 +
                "[registry](inventory/scenario-records.json)\n").encode()
        other.source.pop(GAP_PATH)
        other.source[path] = body
        other.put(path, body)
        parent_paths.append(path)
        gap = path + "#" + GAP_ID
    other.parent = other.commit("fixture second generation parent", parent_paths)
    other.after += b"\n# second independently retained revision\n"
    other.put(other.accepted_path, other.after)
    other.accepted = other.commit("fixture second accepted Source", [other.accepted_path])
    manifest = {path: digest(body) for path, body in other.source.items()}
    manifest[other.accepted_path] = None
    other.identity.update(gap_reference=gap, source_manifest=manifest, source_sha256=digest(canonical(manifest)))
    other.identity["fingerprint"] = digest(canonical({key: other.identity[key] for key in
        ("stage_id", "gap_reference", "scenario_key", "symbol_groups", "source_sha256")}))
    other.work = digest(canonical({"kind": "two-site-logical-work", "schema_version": 1,
        **{key: other.identity[key] for key in ("stage_id", "gap_reference", "scenario_key", "symbol_groups")}}))
    other.task_id, other.controller, other.prefix = "retained-task-2", "retained-controller-2", "retained-proof-2"
    other.worker, other.reviewer = other.prefix + "-worker-0", other.prefix + "-reviewer-0"
    other.task["task_id"] = other.task_id
    bundle = {"head": other.parent, "gap_reference": gap, "excerpts": other.excerpts,
              "semantic_identity": other.identity}
    other.task["goal"] = "Retained second revision.\nCANONICAL_SOURCE_EXCERPTS_JSON:\n" + canonical(bundle).decode()
    other.sources = {path: {"content": other.source[path].decode(), "sha256": digest(other.source[path])}
                     for path in other.context}
    other.sources[other.accepted_path] = {"content": None, "sha256": None}
    other.worker_snapshot = {"task": other.task, "source_head": other.parent, "sources": other.sources}
    other.proposal["changes"][0]["content"] = other.after.decode()
    for status, run in ((other.worker_status, other.worker), (other.reviewer_status, other.reviewer)):
        status.update(run_id=run, task_id=other.task_id, head=other.parent, controller_run_id=other.controller)
    other.reviewer_status["input_worker_run_id"] = other.worker
    other.root_result.update(commit=other.accepted, gemini_worker_run_id=other.worker,
                             gemini_reviewer_run_id=other.reviewer)
    other.root_status.update(head_before=other.parent, head_after=other.accepted,
                             gemini_worker_run_id=other.worker, gemini_reviewer_run_id=other.reviewer)
    rewrite_chain(other)
    # publish uses the form's filename; retain BOTH original receipts without rewriting their bytes.
    other.receipt_path = INVENTORY + "fixture-second-inline.json"
    other.put(other.receipt_path, raw(other.receipt))
    other.put(repo.receipt_path, original_receipt)
    other.commit("fixture retain both revisions", [other.receipt_path, repo.receipt_path])
    for row in other.rows:
        row[6] = row[9] = other.receipt_path
    other.index["rows"] = original_rows + other.rows
    other.write_index()
    other.metric["gap_reference"] = gap
    other.metrics = [repo.metric, other.metric]
    other.artifact(METRICS, other.metrics)
    repo.head, repo.ledger = other.head, other.ledger
    repo.artifacts[METRICS] = raw(other.metrics)
    return other


def test_source_revisions_are_one_work_and_whole_permutation_keeps_proof_closure(tmp_path, monkeypatch):
    first = ProofRepository(tmp_path / "repo")
    second = revision(first)
    assert first.work == second.work and first.identity["fingerprint"] != second.identity["fingerprint"]
    value, observed = collect(second, monkeypatch)
    assert value["unknown"] == []
    expected_work = {"work_fingerprint": first.work,
        "semantic_fingerprints": sorted([first.identity["fingerprint"], second.identity["fingerprint"]]),
        "source_revisions": sorted([{"semantic_fingerprint": repo.identity["fingerprint"],
            "source_sha256": repo.identity["source_sha256"], "source_head": repo.parent,
            "accepted_source_commit": repo.accepted} for repo in (first, second)], key=canonical),
        "evidence_level": "ORIGINAL_ROOT_RECEIPT_CORROBORATED",
        "proof_refs": sorted({(r["path"], r["sha256"], r["pointer"]): r
            for repo in (first, second) for r in expected_refs(repo)}.values(),
            key=lambda r: (r["path"], r["sha256"], r["pointer"])),
        "retained_run_ids": [first.controller, second.controller]}
    assert value["works"] == [expected_work]
    expected_scenarios = [{"work_fingerprint": first.work, "gap_reference": GAP,
        "canonical_symbol": "src/market.py:handle_" + name, "scenario_key": name + "-literal",
        "witnesses": sorted([{"selector": repo.selectors[index], "source_commit": repo.accepted,
            "source_file_sha256": digest(repo.after)} for repo in (first, second)], key=canonical)}
        for index, name in enumerate(("buy", "sell"))]
    assert value["handler_scenarios"] == expected_scenarios
    assert value["retention_limits"]["proved_index_ordinals"] == [0, 1, 2, 3]
    assert value["snapshot"]["bytes_read"] == sum(len(item[2]) for item in observed)
    second.index["rows"] = list(reversed(second.index["rows"]))
    second.write_index()
    reordered, _ = collect(second, monkeypatch)
    # Ledger/HEAD/proof ledger SHA change honestly; all OTHER work refs stay invariant.
    def without_ledger(works):
        detached = copy.deepcopy(works)
        for work in detached:
            work["proof_refs"] = [ref for ref in work["proof_refs"] if ref["path"] != LEDGER]
        return detached
    assert without_ledger(reordered["works"]) == without_ledger(value["works"])
    assert reordered["handler_scenarios"] == expected_scenarios


def test_strict_parser_resource_exceptions_and_fixed_typed_failures():
    for body in (b'{"a":1,"a":2}', b'NaN', b'"\\ud800"', b'[[', b'\xff',
                 b'[' * 33 + b'0' + b']' * 33):
        with pytest.raises(attest._ProofIssue) as caught:
            attest._json(body, lambda: None)
        assert str(caught.value) == "MALFORMED_EVIDENCE"
    for code in ("TIME_LIMIT", "COUNT_LIMIT", "CUSTODY_DRIFT"):
        def tick():
            raise custody._EvidenceSnapshotError(code)
        with pytest.raises(custody._EvidenceSnapshotError) as caught:
            attest._json(b'{"safe":1}', tick)
        assert caught.value.code == code
    assert attest._json(b'{"unicode":"\\u06cc","integer":1}', lambda: None) == {"unicode": "ی", "integer": 1}
