"""Finite historical DATA admission and unchanged real-Git source proofs."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import two_site_cycle_tools as subject
from tests.test_two_site_canonical_evidence_inspect import _make_canonical_repo, _run_git

PUBLIC_ROOT = Path(subject.runner.__file__).resolve().parents[1]
INV = "docs/refactor/two-server/section-1/inventory/"
MANIFEST = INV + "seed-handler-coverage.json"
OWNERS = {
    'business_768_market_overrides_a2_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-market-overrides-a2.json',
        '0d0309ad5e1fc40f4124fd82cdf968bb2894f45767fb952b3ce4eb5f3888dec2'),
    'business_768_market_overrides_b3_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-market-overrides-b3.json',
        'af9f4670001c071f76cfb75f99ce1b6929e6f6742354bf057cf2ff1bdcb111ab'),
    'business_768_live786_manual783_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-manual783-provenance.json',
        '685032585be7452977b4fda3bf4dbb2cbaf37c127131743a69c1fdf233ac9a5f'),
    'business_768_live786_five_source_closeout_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-five-source-closeout-manual.json',
        '46b231dbcf808f556bb5f798c50fc9444a4e6fc7ce1dfa8ec5e6d8855567a4d3'),
    'business_768_live786_reset_execute_singleton_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-reset-execute-singleton.json',
        '4ef4ffe40f33dfe4328573bdca380b220e33306e1bee5f18d5baf54aef7170a2'),
    'business_768_live786_replace_execute_singleton_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-replace-execute-singleton.json',
        'cf251e42b72c41490e205499dbc269704d75ba422dbf730ef1027d29101d579c'),
    'business_768_live786_verifier_only_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-verifier-only.json',
        '09c03b798d7c268a987874c1ebe3e834ed534edc97eade4be9b68e097d27f960'),
    'business_768_live786_authority_registration_after_security_path': (
        'docs/refactor/two-server/section-1/inventory/recent-characterization-business-768-live786-authority-registration-security.json',
        '676426367cb4fd92ace6cd9f2bd04c90fb70bc8f24e357c3fe4293004f56c113'),
    'business_768_current_session_body_registration_path': (
        'docs/refactor/two-server/section-1/inventory/current-session-body-registration.json',
        '9ef13f20a2c85cc813e79e6e209398cddb25c6f60a66eb12e47013d63cc49049'),
    'business_768_current_session_group_union_path': (
        'docs/refactor/two-server/section-1/inventory/current-session-five-source-union.json',
        '5d9734c108edd3269cd56c264bed8079ded467d5c63a648c84db6eb639f6ab82'),
    'business_768_current_session_group_closeout_path': (
        'docs/refactor/two-server/section-1/inventory/current-session-five-source-closeout.json',
        '8eabc7b2fde077e6404eaee184a359ad2dc79add1a4a7b5ab1a2ed2d5c6b31b2'),
    'business_768_post_security_five_registration_path': (
        'docs/refactor/two-server/section-1/inventory/post-security-five-source-registration.json',
        '56f5d4e76c4cd9edc97689fda6025e9fae9e059a80f3c8a995f457fa7d709bbe'),
    'business_768_post_security_five_closeout_path': (
        'docs/refactor/two-server/section-1/inventory/post-security-five-source-closeout.json',
        '2bfe90e91579d0457b7a3d8008733b53f90d6af5033c4111e33f238e28d934fd'),
}


def _json(repo, path):
    return json.loads((repo / path).read_bytes())


def _commit(repo, changes):
    for path, value in changes.items():
        raw = value if type(value) is bytes else (json.dumps(value, indent=2) + "\n").encode()
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    _run_git(repo, "add", "--", *changes)
    _run_git(repo, "commit", "-m", "owned historical-owner fixture")
    return _run_git(repo, "rev-parse", "HEAD")


def _fixture(tmp_path, monkeypatch, *, adapt_proof_commits=False):
    repo = (tmp_path / "historical-owner-lab").resolve()
    _make_canonical_repo(repo)
    documents = {key: _json(PUBLIC_ROOT, path) for key, (path, _) in OWNERS.items()}
    seeds = {seed for doc in documents.values() for row in doc["records"] for seed in row["seed_ids"]}
    assert len(seeds) == 8
    if adapt_proof_commits:
        # Only this real-Git fixture adapts record Source/hash/commit bindings. The separate
        # raw-DATA cases below exercise the actual unmodified accepted hashes.
        paths = set()
        for doc in documents.values():
            for row in doc["records"]:
                path = row["path"]
                # Synthetic Source bindings inside this disposable Git repo:
                # exact recorded selector names and a distinct original-version
                # marker retain historical/current revisions and the one alias.
                # No original Git object DB or private runtime data is required.
                classes, functions = {}, []
                for selector in row["test_selectors"]:
                    parts = selector.split("::")[1:]
                    if len(parts) == 1:
                        functions.append("def " + parts[0] + "(): pass\n")
                    else:
                        classes.setdefault(parts[0], []).append(
                            " def " + parts[1] + "(self): pass\n")
                text = ("# Adapted fixture version " + row["sha256"] + "\n"
                        + "".join(functions) + "".join(
                            "class " + name + ":\n" + "".join(methods)
                            for name, methods in classes.items()))
                raw = text.encode()
                if not (repo / path).exists() or (repo / path).read_bytes() != raw:
                    _commit(repo, {path: raw})
                row["sha256"] = hashlib.sha256(raw).hexdigest()
                row["utf8_bytes"] = len(raw)
                row["last_commit"] = _run_git(repo, "log", "-1", "--format=%H", "--", path)
                paths.add(path)
        assert len(paths) == 18
    manifest = _json(repo, MANIFEST)
    surface_path = manifest["inventory"]["paths"][0]
    surface = _json(repo, surface_path)
    surface["entries"].extend({"behavior_id": seed} for seed in sorted(seeds))
    manifest["inventory"]["expected_seed_count"] += len(seeds)
    manifest["counts"]["reconciled_new_seed_count"] += len(seeds)
    manifest["counts"]["evidenced_seed_count"] += len(seeds)
    scenario_path = manifest["evidence_sources"]["baseline_path"]
    scenario = _json(repo, scenario_path)
    scenario["seed_mapping"]["expected_seed_count"] += len(seeds)
    changes = {MANIFEST: manifest, surface_path: surface, scenario_path: scenario}
    fixture_pins = {}
    for key, (path, sha) in OWNERS.items():
        manifest["evidence_sources"][key] = path
        raw = ((json.dumps(documents[key], indent=2) + "\n").encode()
               if adapt_proof_commits else (PUBLIC_ROOT / path).read_bytes())
        changes[path] = raw
        fixture_pins[key] = (path, hashlib.sha256(raw).hexdigest())
        if not adapt_proof_commits:
            assert fixture_pins[key][1] == sha
    _commit(repo, changes)
    if adapt_proof_commits:
        monkeypatch.setattr(subject, "_HISTORICAL_OWNER_RECEIPTS", fixture_pins)
    return repo, documents, seeds, fixture_pins


def test_all_thirteen_actual_unmodified_owner_bytes_and_records_are_admitted(tmp_path, monkeypatch):
    assert subject._HISTORICAL_OWNER_RECEIPTS == OWNERS
    repo, documents, seeds, _ = _fixture(tmp_path, monkeypatch)
    assert len(documents) == 13
    assert sum(len(doc["records"]) for doc in documents.values()) == 21
    result = subject.inspect_canonical_evidence(repo)
    assert result["status"] == "VALID", result
    # Actual Source paths are intentionally untracked here: the common
    # proof loop must classify them UNKNOWN, never skip them or manufacture proof.
    assert {result["coverage"][seed] for seed in seeds} == {"UNKNOWN"}


def test_real_source_files_and_selectors_use_ordinary_proof_loop(tmp_path, monkeypatch):
    repo, _, seeds, _ = _fixture(tmp_path, monkeypatch, adapt_proof_commits=True)
    result = subject.inspect_canonical_evidence(repo)
    assert result["status"] == "VALID", result
    assert {result["coverage"][seed] for seed in seeds} == {"PROVEN_FOR_REQUESTED_SCENARIO"}


@pytest.mark.parametrize("fault", ["path", "key", "unknown_namespace", "raw_sha", "frozen_gate"])
def test_finite_identity_and_original_raw_hash_are_mandatory(tmp_path, monkeypatch, fault):
    repo, _, _, _ = _fixture(tmp_path, monkeypatch)
    key = "business_768_current_session_body_registration_path"
    path, _ = OWNERS[key]
    manifest = _json(repo, MANIFEST)
    if fault == "path":
        manifest["evidence_sources"][key] = INV + "recent-characterization-impostor.json"
        changes = {MANIFEST: manifest}
    elif fault == "key":
        manifest["evidence_sources"]["unbound_historical_owner_path"] = manifest["evidence_sources"].pop(key)
        changes = {MANIFEST: manifest}
    elif fault == "unknown_namespace":
        manifest["evidence_sources"]["unknown_owner_path"] = INV + "unknown-owner.json"
        changes = {MANIFEST: manifest}
    elif fault == "raw_sha":
        changes = {path: (repo / path).read_bytes() + b"\n"}
    else:
        doc = _json(repo, path)
        doc["closures_authorized"] = True
        changes = {path: doc}
    _commit(repo, changes)
    result = subject.inspect_canonical_evidence(repo)
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "historical owner" in result["reason"] or "inventory or sources" in result["reason"]


@pytest.mark.parametrize("fault", ["source_sha", "selector", "nonancestor", "unknown_seed"])
def test_admission_does_not_skip_source_sha_selector_ancestry_or_seed_checks(tmp_path, monkeypatch, fault):
    repo, docs, _, pins = _fixture(tmp_path, monkeypatch, adapt_proof_commits=True)
    key = "business_768_current_session_body_registration_path"
    path, _ = OWNERS[key]
    doc = copy.deepcopy(docs[key])
    row = doc["records"][0]
    affected = row["seed_ids"][0]
    if fault == "source_sha":
        row["sha256"] = "0" * 64
    elif fault == "selector":
        row["test_selectors"] = [row["path"] + "::MissingClass::test_missing"]
    elif fault == "nonancestor":
        row["last_commit"] = "f" * 40
    else:
        row["seed_ids"] = ["ABSENT-FROM-ACTUAL-SURFACE"]
    manifest = _json(repo, MANIFEST)
    # Isolate the body's two seeds from other independently valid Source rows;
    # another valid proof of the same seed must not mask this fixture's fault.
    for other in OWNERS:
        if other != key:
            manifest["evidence_sources"].pop(other)
    _commit(repo, {path: doc, MANIFEST: manifest})
    # Rebind only the fixture's admitted DATA; the ordinary record checks must
    # still detect the independently broken Source/selector/commit or seed.
    pins[key] = (path, hashlib.sha256((repo / path).read_bytes()).hexdigest())
    result = subject.inspect_canonical_evidence(repo)
    if fault in {"nonancestor", "unknown_seed"}:
        assert result["status"] == "INVENTORY_INCONSISTENT", result
    else:
        assert result["status"] == "VALID", result
        assert result["coverage"][affected] == "UNKNOWN"


def test_the_one_frozen_alias_keeps_both_metadata_and_executes_both_proofs(tmp_path, monkeypatch):
    repo, docs, _, _ = _fixture(tmp_path, monkeypatch, adapt_proof_commits=True)
    keys = tuple(subject._HISTORICAL_PROOF_ALIAS_OWNERS)
    path = "tests/test_p1_00b_characterization_market_overrides_all_failure_sqlite.py"
    records = [next(row for row in docs[key]["records"] if row["path"] == path) for key in keys]
    assert records[0] != records[1]  # Preserve the different frozen wrapper metadata.
    assert [records[0].get(field) for field in subject._HISTORICAL_PROOF_FIELDS] == [
        records[1].get(field) for field in subject._HISTORICAL_PROOF_FIELDS]
    original = subject._resolve_selectors_in_source
    observations = []

    def observe(cells, selectors, source_path):
        if source_path == path:
            observations.append(tuple(selectors))
        return original(cells, selectors, source_path)

    monkeypatch.setattr(subject, "_resolve_selectors_in_source", observe)
    result = subject.inspect_canonical_evidence(repo)
    assert result["status"] == "VALID", result
    assert len(observations) == 2 and observations[0] == observations[1]
    seed = records[0]["seed_ids"][0]
    assert result["coverage"][seed] == "PROVEN_FOR_REQUESTED_SCENARIO"
    assert list(result["coverage"]).count(seed) == 1


@pytest.mark.parametrize("fault", ["projection_value", "projection_type", "other_owner_pair", "third_new_owner"])
def test_no_changed_other_or_third_current_alias_is_admitted(tmp_path, monkeypatch, fault):
    repo, docs, _, pins = _fixture(tmp_path, monkeypatch, adapt_proof_commits=True)
    key = "business_768_live786_five_source_closeout_path"
    path, _ = OWNERS[key]
    doc = copy.deepcopy(docs[key])
    shared_path = "tests/test_p1_00b_characterization_market_overrides_all_failure_sqlite.py"
    row = next(row for row in doc["records"] if row["path"] == shared_path)
    changes = {}
    if fault in {"projection_value", "projection_type"}:
        row["utf8_bytes"] = row["utf8_bytes"] + 1 if fault == "projection_value" else str(row["utf8_bytes"])
        changes[path] = doc
    elif fault == "other_owner_pair":
        key = "business_768_current_session_group_union_path"
        path, _ = OWNERS[key]
        doc = copy.deepcopy(docs[key])
        doc["records"] = copy.deepcopy(docs["business_768_current_session_body_registration_path"]["records"])
        changes[path] = doc
    else:
        path = INV + "recent-characterization-third-standard.json"
        head = _run_git(repo, "rev-parse", "HEAD")
        changes[path] = {"schema_version": 1, "stage": "P1-00b", "assignment_id": "WA-THIRD",
                         "status": "PARTIAL_CANONICAL_INTEGRATION", "receipt_type": "PARTIAL_CANONICAL_INTEGRATION",
                         "source": {"accepted_source_commit": head, "accepted_parent": head}, "records": [row],
                         "coverage_status": "PARTIAL", "seed_acceptance_status": "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
                         "closures_authorized": False, "canonical_blockers": sorted(subject._CANONICAL_BLOCKERS),
                         "stages": {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"}}
        manifest = _json(repo, MANIFEST)
        manifest["evidence_sources"]["third_standard_path"] = path
        changes[MANIFEST] = manifest
    _commit(repo, changes)
    if fault != "third_new_owner":
        # Deliberately qualify the adapted fixture DATA, then test the distinct
        # current-proof alias restriction rather than stopping at the SHA guard.
        pins[key] = (path, hashlib.sha256((repo / path).read_bytes()).hexdigest())
    result = subject.inspect_canonical_evidence(repo)
    assert result["status"] == "INVENTORY_INCONSISTENT", result
    assert "conflicting current evidence" in result["reason"]


@pytest.mark.parametrize("fault", ["status", "receipt_type", "source", "source_ancestor", "closure_gate"])
def test_new_standard_receipts_keep_the_strict_existing_envelope(tmp_path, monkeypatch, fault):
    repo, _, _, _ = _fixture(tmp_path, monkeypatch)
    head = _run_git(repo, "rev-parse", "HEAD")
    path = INV + "recent-characterization-new-standard.json"
    doc = {"schema_version": 1, "stage": "P1-00b", "assignment_id": "WA-NEW",
           "status": "PARTIAL_CANONICAL_INTEGRATION", "receipt_type": "PARTIAL_CANONICAL_INTEGRATION",
           "source": {"accepted_source_commit": head, "accepted_parent": head}, "records": [],
           "coverage_status": "PARTIAL", "seed_acceptance_status": "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
           "closures_authorized": False, "canonical_blockers": sorted(subject._CANONICAL_BLOCKERS),
           "stages": {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"}}
    if fault in {"status", "receipt_type"}:
        doc[fault] = "REGISTERED_ON_EXACT_ACCEPTANCE"
    elif fault == "source":
        doc.pop("source")
    elif fault == "source_ancestor":
        doc["source"]["accepted_source_commit"] = "f" * 40
    else:
        doc["closures_authorized"] = True
    manifest = _json(repo, MANIFEST)
    manifest["evidence_sources"]["new_standard_path"] = path
    _commit(repo, {MANIFEST: manifest, path: doc})
    result = subject.inspect_canonical_evidence(repo)
    assert result == {"status": "INVENTORY_INCONSISTENT", "reason": "independent supplemental receipt invalid: " + path}
