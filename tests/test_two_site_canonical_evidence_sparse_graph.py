"""Independent temporary-Git custody tests for sparse inventory provenance."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from scripts import two_site_cycle_tools as subject
from tests.test_two_site_canonical_evidence_inspect import _make_canonical_repo, _run_git

INV = "docs/refactor/two-server/section-1/inventory/"
MANIFEST = INV + "seed-handler-coverage.json"
PARENT = INV + "recent-characterization-continuation-2.json"
THIRD = INV + "continuation-3.json"
FOURTH = INV + "recent-characterization-continuation-4.json"
PARTIAL = "BOT-DEMO-SEED-PARTIAL"


def doc(root, path):
    return json.loads((root / path).read_text())


def put(root, path, value):
    assert root.is_relative_to(Path("/tmp")) and root.name == "sparse-lab"
    (root / path).write_text(json.dumps(value, indent=2) + "\n")
    _run_git(root, "add", "--", path)
    _run_git(root, "commit", "-m", "independent LAB fixture")


def link(root, path):
    return {"path": path,
            "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
            "last_commit": _run_git(root, "log", "-1", "--format=%H", "HEAD", "--", path)}


def rebind(root, parent):
    put(root, PARENT, parent)
    third = doc(root, THIRD)
    third["predecessor_inventory"] = link(root, PARENT)
    put(root, THIRD, third)
    fourth = doc(root, FOURTH) if (root / FOURTH).exists() else {
        "schema_version": 1, "stage": "P1-00b", "records": []}
    fourth["predecessor_inventory"] = link(root, THIRD)
    put(root, FOURTH, fourth)


def fixture(tmp_path):
    root = (tmp_path / "sparse-lab").resolve()
    _make_canonical_repo(root)
    parent = doc(root, INV + "continuation-2.json")
    record = copy.deepcopy(doc(root, INV + "base.json")["records"][0])
    record["seed_ids"] = [PARTIAL]
    parent["records"] = [record]
    rebind(root, parent)
    manifest = doc(root, MANIFEST)
    del manifest["evidence_sources"]["continuation_2_path"]
    manifest["evidence_sources"]["continuation_4_path"] = FOURTH
    put(root, MANIFEST, manifest)
    return root, parent


def view(root):
    files = [MANIFEST, PARENT, THIRD, FOURTH]
    return (_run_git(root, "rev-parse", "HEAD"),
            _run_git(root, "status", "--porcelain=v1", "--untracked-files=all"),
            {path: hashlib.sha256((root/path).read_bytes()).hexdigest()
             if (root/path).exists() else None for path in files})


def inspect_unchanged(root, expected):
    before = view(root)
    result = subject.inspect_canonical_evidence(root, PARTIAL)
    assert view(root) == before
    assert result["status"] == expected, result
    return result


def test_predecessor_only_record_never_becomes_coverage(tmp_path):
    root, _ = fixture(tmp_path)
    result = inspect_unchanged(root, "VALID")
    assert result["seed_count"] == result["expected_seed_count"] == 4
    assert result["requested_coverage"] == "PARTIAL_OR_SEED_ONLY"
    selected = subject.inspect_canonical_evidence(root, "API-DEMO-SEED-PROVEN")
    assert selected["status"] == "VALID"
    assert selected["requested_coverage"] == "PROVEN_FOR_REQUESTED_SCENARIO"


@pytest.mark.parametrize("fault", ["stage", "records", "digest", "commit", "cycle"])
def test_bad_parent_with_rebound_surroundings_and_exact_restore(tmp_path, fault):
    root, good = fixture(tmp_path)
    assert inspect_unchanged(root, "VALID")["requested_coverage"] == "PARTIAL_OR_SEED_ONLY"
    bad = copy.deepcopy(good)
    if fault == "stage":
        bad["stage"] = "P2"
    elif fault == "records":
        bad["records"] = None
    elif fault == "digest":
        bad["predecessor_inventory"]["sha256"] = "0" * 64
    elif fault == "commit":
        bad["predecessor_inventory"]["last_commit"] = "0" * 40
    else:
        bad["predecessor_inventory"] = link(root, THIRD)
    rebind(root, bad)
    inspect_unchanged(root, "INVENTORY_INCONSISTENT")
    rebind(root, good)
    assert inspect_unchanged(root, "VALID")["requested_coverage"] == "PARTIAL_OR_SEED_ONLY"


def test_missing_parent_rejected_without_mutation_and_exact_restoration(tmp_path):
    root, _ = fixture(tmp_path)
    inspect_unchanged(root, "VALID")
    path = root / PARENT
    raw = path.read_bytes()
    path.unlink()  # Exact temporary LAB fixture only.
    inspect_unchanged(root, "INVENTORY_INCONSISTENT")
    path.write_bytes(raw)
    inspect_unchanged(root, "VALID")


@pytest.mark.parametrize("fault", ["leading_zero", "out_of_range", "unknown_key",
                                  "duplicate_path", "conflicting_parent_identity"])
def test_source_identity_fault_and_restoration(tmp_path, fault):
    root, _ = fixture(tmp_path)
    good = doc(root, MANIFEST)
    inspect_unchanged(root, "VALID")
    bad = copy.deepcopy(good)
    sources = bad["evidence_sources"]
    if fault == "leading_zero":
        sources["continuation_03_path"] = sources.pop("continuation_3_path")
    elif fault == "out_of_range":
        sources["continuation_65_path"] = sources.pop("continuation_3_path")
    elif fault == "unknown_key":
        sources["other_path"] = sources.pop("continuation_3_path")
    elif fault == "duplicate_path":
        sources["continuation_4_path"] = sources["continuation_3_path"]
    else:
        sources["continuation_2_path"] = INV + "continuation-2.json"
    put(root, MANIFEST, bad)
    inspect_unchanged(root, "INVENTORY_INCONSISTENT")
    put(root, MANIFEST, good)
    inspect_unchanged(root, "VALID")
