"""No missing-history shortcut may hide contradictions, custody or exhausted budgets."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import ast
import copy
import errno
import json
from pathlib import Path
import traceback

import pytest

from scripts import two_site_accepted_work_attestation as attest
from scripts import two_site_accepted_work_evidence as files
from scripts import two_site_accepted_work_proofs as subject
from scripts import two_site_accepted_work_snapshot as custody
from tests.test_two_site_accepted_work_attestation import (
    ProofRepository, LEDGER, INVENTORY, GAP, GAP_ID, METRICS, PRIVATE, USAGE,
    canonical, raw, digest, fixed, read_counter,
)
from tests.test_two_site_accepted_work_proofs import (
    collect, whole_oracle, expected_output, expected_refs, assert_unknown, revision,
)


def file_error(code, operation):
    with pytest.raises(files._EvidenceFilesError) as caught:
        operation()
    assert caught.value.code == code and str(caught.value) == code
    assert caught.value.__suppress_context__ is True
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))
    return caught.value


@pytest.mark.parametrize("layout,kind,missing", [
    ("runs", "root_result", "runs"), ("runs", "root_status", "controller"),
    ("flat", "root_result", "controller"), ("flat", "root_status", "leaf")])
def test_root_only_first_missing_descendant_guard_and_appearance(tmp_path, layout, kind, missing):
    root = tmp_path / "root"
    anchor = root / ".local/two-site-refactor/autopilot"
    anchor.mkdir(mode=0o700, parents=True)
    parent = anchor / ("runs/controller" if layout == "runs" else "controller")
    if missing == "controller" and layout == "runs":
        parent.parent.mkdir(mode=0o700)
    if missing == "leaf":
        parent.mkdir(mode=0o700)
    path, _ = files._artifact(kind, (layout, "controller"))
    with pytest.raises(files._EvidenceFilesError) as final:
        with files._evidence_files(root) as owner:
            initial = owner._metadata()
            file_error("MISSING_FILE", lambda: owner._read(kind, (layout, "controller")))
            record = owner._records[path]
            expected_name = "runs" if missing == "runs" else "controller" if missing == "controller" else kind[5:] + ".json"
            assert record["missing"] is True and record["name"] == expected_name
            assert record["fd"] is None and record["data"] is None
            assert path in owner._seen and len(owner._seen) == 1
            assert owner._metadata() == initial  # No fake file/bytes receipt for absence.
            file_error("MISSING_FILE", lambda: owner._read(kind, (layout, "controller")))
            owner._verify()
            appeared = record["parent"] / record["name"]
            if missing == "leaf":
                appeared.write_bytes(b"{}")
                appeared.chmod(0o600)
            else:
                appeared.mkdir(mode=0o700)
            file_error("CUSTODY_DRIFT", lambda: owner._read(kind, (layout, "controller")))
    assert final.value.code == "CUSTODY_DRIFT"


def test_root_guard_never_crosses_anchor_or_other_families_or_unsafe_parents(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    with files._evidence_files(root) as owner:
        file_error("UNSAFE_PATH", lambda: owner._read("root_result", ("runs", "missing")))
    anchor = root / ".local/two-site-refactor/autopilot"
    anchor.mkdir(mode=0o700, parents=True)
    with files._evidence_files(root) as owner:
        file_error("UNSAFE_PATH", lambda: owner._read("proposal_status", "missing"))
    target = root / "target"
    target.mkdir(mode=0o700)
    (anchor / "runs").symlink_to(target, target_is_directory=True)
    with files._evidence_files(root) as owner:
        file_error("UNSAFE_PATH", lambda: owner._read("root_status", ("runs", "missing")))
    (anchor / "runs").unlink()
    (anchor / "runs").write_bytes(b"not-directory")
    with files._evidence_files(root) as owner:
        file_error("UNSAFE_PATH", lambda: owner._read("root_status", ("runs", "missing")))
    (anchor / "runs").unlink()
    actual = files.os.stat
    def denied(name, *args, **kwargs):
        if name == "runs" and kwargs.get("dir_fd") is not None:
            raise PermissionError(errno.EACCES, PRIVATE)
        return actual(name, *args, **kwargs)
    with files._evidence_files(root) as owner, monkeypatch.context() as patcher:
        patcher.setattr(files.os, "stat", denied)
        file_error("UNSAFE_PATH", lambda: owner._read("root_result", ("runs", "missing")))
        assert not owner._records  # Permission/IO never becomes a negative guard.


def test_root_negative_observations_keep_shared_128_and_metadata_caps(tmp_path):
    root = tmp_path / "root"
    (root / ".local/two-site-refactor/autopilot").mkdir(mode=0o700, parents=True)
    with files._evidence_files(root) as owner:
        expected_charge = 0
        for index in range(128):
            key = ("runs", f"missing-{index}")
            path, _ = files._artifact("root_result", key)
            expected_charge += len(path.encode()) + len(json.dumps(key, ensure_ascii=True, separators=(",", ":")).encode())
            file_error("MISSING_FILE", lambda key=key: owner._read("root_result", key))
        assert len(owner._seen) == len(owner._records) == 128
        assert owner._metadata_bytes == expected_charge and owner._bytes_read == 0
        file_error("COUNT_LIMIT", lambda: owner._read("root_status", ("flat", "extra")))
        assert len(owner._seen) == 128
        owner._verify()


@pytest.mark.parametrize("case", ["duplicate-heading", "wrong-columns", "duplicate-row", "unsafe-receipt", "bad-json"])
def test_index_inventory_is_not_acceptance_and_malformed_index_is_fixed_fatal(tmp_path, monkeypatch, case):
    repo = ProofRepository(tmp_path / "repo")
    if case == "duplicate-heading":
        body = repo.ledger + repo.ledger
    elif case == "bad-json":
        body = repo.ledger.replace(b'"rows":', b'"rows":NaN,"rows":', 1)
    else:
        index = copy.deepcopy(repo.index)
        if case == "wrong-columns":
            index["field_order"] = list(reversed(index["field_order"]))
        elif case == "duplicate-row":
            index["rows"].append(copy.deepcopy(index["rows"][0]))
        else:
            index["rows"][0][6] = "../../" + PRIVATE
        body = b"Reviewed current seven-scenario Source index (data):\n```json\n" + canonical(index) + b"\n```\n"
    repo.put(LEDGER, body)
    repo.commit("fixture malformed canonical index", [LEDGER])
    with custody._proof_snapshot(repo.root) as snapshot:
        fixed("INVALID_CANONICAL_INDEX", lambda: subject._collect_accepted_work_proofs(snapshot))


@pytest.mark.parametrize("body", [b'{}', b'[NaN]', b'[{"a":1,"a":2}]', b'["\\ud800"]'])
def test_bad_metrics_is_not_empty_success_or_dropped_ordinals(tmp_path, body):
    repo = ProofRepository(tmp_path / "repo")
    repo.put(METRICS, body)
    with pytest.raises(custody._EvidenceSnapshotError) as final:
        with custody._proof_snapshot(repo.root) as snapshot:
            fixed("INVALID_METRICS_LEDGER", lambda: subject._collect_accepted_work_proofs(snapshot))
    assert final.value.code == "JSON_INVALID"


@pytest.mark.parametrize("fault", ["parse-loop", "parse-post", "metadata", "custody", "bytes"])
def test_shared_owner_fatals_poison_and_resurface_not_unknown(tmp_path, monkeypatch, fault):
    repo = ProofRepository(tmp_path / "repo")
    code = {"parse-loop": "TIME_LIMIT", "parse-post": "TIME_LIMIT", "metadata": "COUNT_LIMIT",
            "custody": "CUSTODY_DRIFT", "bytes": "BYTE_LIMIT"}[fault]
    fired = []
    with pytest.raises(custody._EvidenceSnapshotError) as final:
        with custody._proof_snapshot(repo.root) as snapshot:
            with monkeypatch.context() as patcher:
                if fault.startswith("parse-"):
                    actual_loads, actual_clock = attest.json.loads, custody.time.monotonic
                    expired = [False]
                    def clock():
                        return snapshot._files._deadline if expired[0] else actual_clock()
                    def loads(text, *args, **kwargs):
                        value = actual_loads(text, *args, **kwargs)
                        if fault == "parse-post":
                            expired[0] = True
                            fired.append(True)
                        return value
                    patcher.setattr(custody.time, "monotonic", clock)
                    if fault == "parse-post":
                        patcher.setattr(attest.json, "loads", loads)
                    else:
                        actual_tick = attest._Attestations.tick
                        def tick(context):
                            actual_tick(context)
                            expired[0] = True
                            fired.append(True)
                        patcher.setattr(attest._Attestations, "tick", tick)
                elif fault == "metadata":
                    actual = snapshot._files._charge_metadata
                    def charging(key, charge):
                        actual(key, charge)
                        if key[0] == "proof":
                            fired.append(True)
                            actual(("test-fault", "over-hard-64K"), 65537)
                    patcher.setattr(snapshot._files, "_charge_metadata", charging)
                elif fault == "custody":
                    snapshot._read("task", repo.task_id)
                    leaf = repo.root / repo.task_path
                    leaf.write_bytes(leaf.read_bytes() + b" ")
                    fired.append(True)
                else:
                    for index in range(4):
                        name = f"budget-{index}.json"
                        repo.put(INVENTORY + name, b" " * 1998000)
                        snapshot._read("receipt", name)
                    fired.append(True)
                error = fixed(code, lambda: subject._collect_accepted_work_proofs(snapshot))
                assert error.__suppress_context__ is True and fired
                assert PRIVATE not in "".join(traceback.format_exception(error))
                with pytest.raises(custody._EvidenceSnapshotError) as closed:
                    snapshot._metadata()
                assert closed.value.code == "CLOSED_SNAPSHOT"
    assert final.value.code == code


def test_dead_closed_wrong_owner_and_unsafe_root_route_do_not_return_partial_success(tmp_path):
    fixed("CLOSED_SNAPSHOT", lambda: subject._collect_accepted_work_proofs({"accepted": True}))
    repo = ProofRepository(tmp_path / "repo")
    manager = custody._proof_snapshot(repo.root)
    with manager as snapshot:
        pass
    fixed("CLOSED_SNAPSHOT", lambda: subject._collect_accepted_work_proofs(snapshot))
    flat = repo.root / f".local/two-site-refactor/autopilot/{repo.controller}"
    flat.symlink_to(repo.root / ".local/two-site-refactor/tasks", target_is_directory=True)
    with pytest.raises(subject._AcceptedWorkProofError) as caught:
        with custody._proof_snapshot(repo.root) as snapshot:
            subject._collect_accepted_work_proofs(snapshot)
    assert caught.value.code == "UNSAFE_PATH"


def test_target_locator_hash_conflict_and_reference_cycle_are_quarantined(tmp_path, monkeypatch):
    repo = ProofRepository(tmp_path / "repo", form="recovery")
    def wrong_anchor(receipt):
        receipt["provenance_references"]["registration"]["sha256"] = "0" * 64
    repo.publish(wrong_anchor)
    value, _ = collect(repo, monkeypatch)
    assert_unknown(value, "HASH_MISMATCH")
    repo.publish()
    # Pure cycle guard: the actual committed document is already in the traversal stack.
    # No fabricated self-SHA/collision is claimed as a valid committed chain.
    with custody._proof_snapshot(repo.root) as snapshot:
        resolver = attest._Attestations(snapshot)
        key = (repo.receipt_path, digest(raw(repo.receipt)), repo.head)
        with pytest.raises(attest._ProofIssue) as caught:
            resolver._walk(repo.receipt, *key, stack=(key,))
        assert caught.value.code == "REFERENCE_CONFLICT"


def test_unsupported_markdown_gap_is_unknown_not_rewritten_or_credited(tmp_path, monkeypatch):
    first = ProofRepository(tmp_path / "repo")
    second = revision(first, markdown=True)
    value, _ = collect(second, monkeypatch)
    assert len(value["works"]) == 1 and value["works"][0]["work_fingerprint"] == first.work
    rows = [entry for entry in value["unknown"] if entry["index_ordinals"]]
    assert {entry["reason_code"] for entry in rows} == {"UNSUPPORTED_GAP_SCHEMA"}
    assert {entry["work_fingerprint"] for entry in rows} == {second.work}
    assert {entry["semantic_fingerprint"] for entry in rows} == {second.identity["fingerprint"]}
    assert sorted(i for entry in rows for i in entry["index_ordinals"]) == [2, 3]
    assert value["retention_limits"]["proved_index_ordinals"] == [0, 1]
    assert value["retention_limits"]["unresolved_index_ordinals"] == [2, 3]


@pytest.mark.parametrize("field", ["resolution_stage", "severity", "owner", "duplicate"])
def test_gap_authority_is_unique_historical_json_not_repeated_mentions(tmp_path, monkeypatch, field):
    first = ProofRepository(tmp_path / "repo")
    second = revision(first, gap_fault=field)
    value, _ = collect(second, monkeypatch)
    assert value["works"] == [] and value["handler_scenarios"] == []
    unknown = [entry for entry in value["unknown"] if entry["index_ordinals"]]
    assert {i: entry["reason_code"] for entry in unknown for i in entry["index_ordinals"]} == {
        0: "IDENTITY_CONFLICT", 1: "IDENTITY_CONFLICT", 2: "SOURCE_MISMATCH", 3: "SOURCE_MISMATCH"}
    for entry in unknown:
        ordinal = entry["index_ordinals"][0]
        repo = first if ordinal < 2 else second
        refs = expected_refs(repo)
        other_handler = "/registered_handlers/" + str(1 - ordinal % 2)
        refs = [ref for ref in refs if not ref["pointer"].startswith(other_handler)]
        if ordinal >= 2:
            retained = {repo.task_path, repo.worker_dir + "task.json", METRICS}
            paths = set(repo.source) | {LEDGER, repo.receipt_path} | retained
            refs = [ref for ref in refs if ref["path"] in paths]
        assert entry["proof_refs"] == refs
        assert entry["work_fingerprint"] == first.work
        assert entry["semantic_fingerprint"] == repo.identity["fingerprint"]
        assert entry["retained_run_ids"] == [repo.controller]
        assert entry["metrics_ordinals"] == [0 if ordinal < 2 else 1]
    assert all(row["state"] == "CONFLICT" for row in value["run_bindings"])
    assert value["retention_limits"]["proved_index_ordinals"] == []
    assert value["retention_limits"]["unresolved_index_ordinals"] == [0, 1, 2, 3]


@pytest.mark.parametrize("form,key", [("login", "accepted_login_actions_registration"), ("recovery", "registration")])
def test_registration_authority_cannot_move_to_arbitrary_matching_provenance(tmp_path, monkeypatch, form, key):
    repo = ProofRepository(tmp_path / "repo", form=form)
    def misnamed(receipt):
        receipt["provenance_references"]["unrelated_locator"] = receipt["provenance_references"].pop(key)
    repo.publish(misnamed)
    value, _ = collect(repo, monkeypatch)
    assert_unknown(value, "REFERENCE_CONFLICT")


def test_optional_status_metadata_is_typed_not_current_model_or_cli_policy(tmp_path, monkeypatch):
    repo = ProofRepository(tmp_path / "repo")
    bad = copy.deepcopy(repo.proposal)
    bad["covered_requirements"] = [True, 2]
    with pytest.raises(attest._ProofIssue) as caught:
        subject._result(bad, repo.task)
    assert caught.value.code == "PROPOSAL_MISMATCH"
    original = copy.deepcopy(repo.reviewer_status)
    for key, value in (("model", []), ("cli_pid", True), ("duration_seconds", float("inf")),
                       ("cli", {"path": 7, "sha256": "0" * 64, "version": "old"}),
                       ("usage", {**USAGE, "total_tokens": True})):
        bad = copy.deepcopy(original)
        bad[key] = value
        if key == "duration_seconds":
            # A nonfinite literal is rejected by the strict parser, not accepted as metadata.
            body = raw(bad)
            repo.artifact(repo.reviewer_dir + "status.json", body)
            result, _ = collect(repo, monkeypatch)
            assert_unknown(result, "MALFORMED_EVIDENCE")
        else:
            repo.artifact(repo.reviewer_dir + "status.json", bad)
            result, _ = collect(repo, monkeypatch)
            assert_unknown(result, "REVIEWER_MISMATCH")
    good = copy.deepcopy(original)
    good.update(model="historical-model-name", effort="historical-effort", cli_pid=123,
                duration_seconds=0.0, usage=None)
    repo.artifact(repo.reviewer_dir + "status.json", good)
    repo.artifacts[repo.reviewer_dir + "status.json"] = raw(good)
    result, observed = collect(repo, monkeypatch)
    whole_oracle(repo, result, observed)


@pytest.mark.parametrize("kind", ["root", "merge"])
def test_accepted_source_must_have_exactly_the_original_generation_parent(tmp_path, monkeypatch, kind):
    repo = ProofRepository(tmp_path / "repo")
    if kind == "root":
        repo.accepted = repo.parent
    else:
        tree = repo.run("rev-parse", repo.accepted + "^{tree}").strip().decode()
        repo.accepted = repo.run("commit-tree", tree, "-p", repo.accepted, "-p", repo.parent,
                                 input=b"fixture merge, not accepted proof\n").strip().decode()
    repo.publish()
    result, _ = collect(repo, monkeypatch)
    assert_unknown(result, "SOURCE_MISMATCH")


def test_canonical_source_task_hash_domains_are_not_interchangeable(tmp_path, monkeypatch):
    repo = ProofRepository(tmp_path / "repo")
    def wrong_domain(receipt):
        receipt["actor"]["generation_task_sha256"] = repo.metric["task_sha256"]
    repo.publish(wrong_domain)
    value, _ = collect(repo, monkeypatch)
    assert_unknown(value, "HASH_MISMATCH")
    repo.publish()
    repo.metric["task_sha256"] = digest(raw(repo.worker_snapshot))
    repo.artifact(METRICS, [repo.metric])
    value, _ = collect(repo, monkeypatch)
    assert_unknown(value, "ACCEPTANCE_MISMATCH")


def test_tier_requires_all_present_original_root_artifacts_not_just_one_route(tmp_path, monkeypatch):
    repo = ProofRepository(tmp_path / "repo", root_receipts=False)
    value, observed = collect(repo, monkeypatch)
    whole_oracle(repo, value, observed)
    # A well-formed original pair improves only the evidence tier, not work cardinality.
    repo.root_receipts = True
    repo.artifact(repo.root_dir + "result.json", repo.root_result)
    repo.artifact(repo.root_dir + "status.json", repo.root_status)
    value, observed = collect(repo, monkeypatch)
    whole_oracle(repo, value, observed)
    flat = f".local/two-site-refactor/autopilot/{repo.controller}/"
    repo.artifact(flat + "result.json", b'{"contradiction":true}')
    value, _ = collect(repo, monkeypatch)
    assert_unknown(value, "HASH_MISMATCH")
    assert value["works"] == []  # NEVER downgrade present contradiction to historical.


def test_exact_caps_and_all_tests_remain_hermetic_source_not_runtime():
    root = Path(__file__).resolve().parents[1]
    limits = {"scripts/two_site_accepted_work_evidence.py": 25000,
              "scripts/two_site_accepted_work_attestation.py": 24000,
              "scripts/two_site_accepted_work_proofs.py": 44000,
              "tests/test_two_site_accepted_work_evidence.py": 24000,
              "tests/test_two_site_accepted_work_attestation.py": 24000,
              "tests/test_two_site_accepted_work_proofs.py": 24000,
              "tests/test_two_site_accepted_work_proofs_faults.py": 24000,
              "docs/refactor/two-server/AE00_ACCEPTED_WORK_PROOFS_CONTRACT.md": 16000,
              "tests/test_two_site_accepted_work_proofs_bindings.py": 24000,
              "docs/refactor/two-server/AE00_WORK_LINEAGE_METRICS_CONTRACT.md": 25000}
    total = 0
    for path, limit in limits.items():
        body = (root / path).read_bytes()
        assert 0 < len(body) <= limit, path
        assert body.endswith(b"\n") and not body.endswith(b"\n\n"), path
        assert all(line.rstrip(b" \t") == line for line in body.split(b"\n")), path
        if path.endswith(".py"):
            ast.parse(body)
        if path.startswith("tests/") and path != "tests/test_two_site_accepted_work_evidence.py":
            text = body.decode()
            assert text.index('os.environ["APP_ENV_FILE"]') < text.index("install_p1_00_network_fence()")
            assert text.index("install_p1_00_network_fence()") < text.index("from scripts")
        total += len(body)
    # MANUAL_AFTERS_UTF8; Gemini Root187K remains unchanged.
    assert total <= 241000


def test_same_live_snapshot_reached_root_guard_mutation_breaks_whole_oracle_then_restores(tmp_path, monkeypatch):
    repo = ProofRepository(tmp_path / "repo")
    reached = []
    with read_counter(monkeypatch) as observed:
        with custody._proof_snapshot(repo.root) as snapshot:
            whole_oracle(repo, subject._collect_accepted_work_proofs(snapshot), observed)
            actual = subject._Collector._roots
            def wrong_tier(collector, *args):
                reached.append(True)
                actual(collector, *args)  # All real artifacts still observed and validated.
                return "HISTORICAL_ATTESTED_CORROBORATED"
            with monkeypatch.context() as patcher:
                patcher.setattr(subject._Collector, "_roots", wrong_tier)
                corrupted = subject._collect_accepted_work_proofs(snapshot)
                assert reached
                with pytest.raises(AssertionError):
                    whole_oracle(repo, corrupted, observed)
            whole_oracle(repo, subject._collect_accepted_work_proofs(snapshot), observed)


def test_metrics_wire_bound_does_not_widen_attestation_or_either_node_guard(tmp_path, monkeypatch):
    from tests.test_two_site_accepted_work_evidence import ArtifactTree, json_once, fixed_error
    # Preserve both old threshold values, now accepted ONLY in the metrics family.
    nodes = [[0] * 98 for _ in range(999)] + [[0] * 1097]
    assert 1 + 999 * 99 + 1098 == 100000
    body = json.dumps(nodes, separators=(",", ":")).encode("ascii")
    tree = ArtifactTree(tmp_path / "metrics-versus-attestation")
    tree.put(METRICS, body)
    assert json_once(tree.root) == attest._json(body, lambda: None) == nodes
    nodes[-1].append(0)
    body = json.dumps(nodes, separators=(",", ":")).encode("ascii")
    tree.put(METRICS, body)
    assert json_once(tree.root) == nodes
    with pytest.raises(attest._ProofIssue) as caught:
        attest._json(body, lambda: None)
    assert caught.value.code == str(caught.value) == "MALFORMED_EVIDENCE"
    assert files._METRICS_MAX_NODES == files._LIMITS["metrics"] // 2 + 1 == 1000001
    # Reach BOTH defensive scans with a deliberately narrowed internal seam.
    # This does NOT claim a valid real-wire cap+1 JSON can fit within2M bytes.
    actual_loads = files.json.loads
    for phase in ("lexical", "parsed"):
        tree.put(METRICS, b"[[0]]" if phase == "lexical" else b"[]")
        reached = []
        def loading(text, *args, **kwargs):
            value = actual_loads(text, *args, **kwargs)
            reached.append(True)
            return [[0]] if phase == "parsed" else value
        with monkeypatch.context() as patcher:
            patcher.setattr(files, "_METRICS_MAX_NODES", 2)
            patcher.setattr(files.json, "loads", loading)
            fixed_error("JSON_INVALID", lambda: json_once(tree.root))
        assert bool(reached) == (phase == "parsed")
    tree.put(METRICS, b"[[0]]")
    assert json_once(tree.root) == [[0]]
