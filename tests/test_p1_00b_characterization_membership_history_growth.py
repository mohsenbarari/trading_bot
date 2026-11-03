"""Historical398 and backed current membership growth; no Git, Provider or child processes."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT
CANDIDATE = ROOT
INVENTORY = "docs/refactor/two-server/section-1/inventory/"
MANIFEST = INVENTORY + "seed-handler-coverage.json"
LEDGER = "docs/refactor/two-server/section-1/00-stage-ledger.md"
OWNERS = (
    "canonical_membership_snapshot",
    "authority_registration_after_security",
    "current_session_body_registration",
    "current_session_group_union",
    "current_session_group_ledger_closeout",
    "post_security_five_registration",
    "post_security_five_ledger_closeout",
)
NEW_PACKET = INVENTORY + "recent-characterization-membership-history-growth.json"
NEW_KEY = "sync_parity_observability_history_growth_path"
BEHAVIOR = "tests/test_p1_00b_characterization_sync_parity_observability_downstream_failures.py"
BEHAVIOR_SHA = "60af10dffb832584b55ab8462af9d7a730a1ef0c76b1983f6069214ed5c39a1f"
SEEDS = (
    "API-SYNC-GET-SYNC-GET-SYNC-PARITY-SNAPSHOT-L4805",
    "API-SYNC-POST-SYNC-RECORD-SYNC-PARITY-STATUS-L4833",
)
SELECTORS = (
    BEHAVIOR + "::SyncParityObservabilityDownstreamFailureCharacterizationTests::"
    "test_parity_snapshot_builder_valueerror_becomes_400_before_server_mode_decoration",
    BEHAVIOR + "::SyncParityObservabilityDownstreamFailureCharacterizationTests::"
    "test_parity_status_redis_store_failure_becomes_503_and_skips_parity_summary_telemetry",
)
OLD_COUNTS = {
    "baseline_evidenced_seed_count": 135,
    "reconciled_new_seed_count": 263,
    "evidenced_seed_count": 398,
    "remaining_seed_count": 67,
}
NEW_COUNTS = {**OLD_COUNTS, "reconciled_new_seed_count": 265,
              "evidenced_seed_count": 400, "remaining_seed_count": 65}


def encoded(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def write_json(root, rel, value):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value))


def read_json(root, rel):
    return json.loads((root / rel).read_bytes())


def fingerprint(value):
    if type(value) is dict:
        return dict, tuple((key, fingerprint(child)) for key, child in value.items())
    if type(value) in (list, tuple):
        return type(value), tuple(fingerprint(child) for child in value)
    return type(value), value


def all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from all_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_strings(child)


@pytest.fixture
def tree(tmp_path):
    """Temp public tree with actual current code and immutable accepted history."""
    root = tmp_path / "repo"
    inventory = root / INVENTORY
    inventory.mkdir(parents=True)
    for original in (SOURCE / INVENTORY).glob("*.json"):
        shutil.copy2(original, inventory / original.name)
    historical_manifest = (SOURCE / "tests/fixtures/p1_00b_membership_history/blobs" /
                           "104bd90d521a6c113b95220dd501d9e556cd93183f5f0cf484f1983710e1089a.txt").read_bytes()
    assert len(historical_manifest) == 5300
    assert hashlib.sha256(historical_manifest).hexdigest() == "104bd90d521a6c113b95220dd501d9e556cd93183f5f0cf484f1983710e1089a"
    (root / MANIFEST).write_bytes(historical_manifest)
    (root / LEDGER).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE / LEDGER, root / LEDGER)
    (root / "tests").mkdir()
    (root / "tests/__init__.py").write_text("")
    # All imported components are stdlib characterization helpers. No app,
    # private .local receipts, credentials, session stores or runtime state.
    for original in (SOURCE / "tests").glob("test_p1_00b_characterization_*.py"):
        shutil.copy2(original, root / "tests" / original.name)
    shutil.copy2(SOURCE / "tests/test_p1_00_characterization_network_isolation.py",
                 root / "tests/test_p1_00_characterization_network_isolation.py")
    for document in inventory.glob("*.json"):
        for rel in all_strings(json.loads(document.read_bytes())):
            if rel.startswith("tests/") and rel.endswith(".py") and ".." not in Path(rel).parts:
                original = SOURCE / rel
                if original.is_file() and not (root / rel).exists():
                    (root / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, root / rel)
    for suffix in OWNERS:
        rel = "tests/test_p1_00b_characterization_" + suffix + ".py"
        assert (CANDIDATE / rel).is_file(), "current owner missing: " + suffix
        shutil.copy2(CANDIDATE / rel, root / rel)
    helper = CANDIDATE / "tests/p1_00b_membership_history.py"
    assert helper.is_file(), "current shared reader missing"
    shutil.copy2(helper, root / "tests/p1_00b_membership_history.py")
    fixtures = CANDIDATE / "tests/fixtures/p1_00b_membership_history"
    assert fixtures.is_dir(), "qualified historical fixtures missing"
    shutil.copytree(fixtures, root / "tests/fixtures/p1_00b_membership_history")

    # Real independent modules with the same public import names and temp ROOTs.
    # Restore the surrounding pytest process's original package references.
    saved = {key: value for key, value in sys.modules.items()
             if key == "tests" or key.startswith("tests.")}
    for key in saved:
        sys.modules.pop(key)
    package = types.ModuleType("tests")
    package.__path__ = [str(root / "tests")]
    sys.modules["tests"] = package
    try:
        modules = {suffix: importlib.import_module(
            "tests.test_p1_00b_characterization_" + suffix) for suffix in OWNERS}
        history = importlib.import_module("tests.p1_00b_membership_history")
        assert history.ROOT == root
        yield root, history, modules
    finally:
        for key in list(sys.modules):
            if key == "tests" or key.startswith("tests."):
                sys.modules.pop(key)
        sys.modules.update(saved)


def assert_all_owners(modules):
    snapshot = modules["canonical_membership_snapshot"]
    value = snapshot.make_canonical_membership_snapshot()
    snapshot.assert_canonical_membership_snapshot(value)
    assert value["counts"] == OLD_COUNTS
    calls = (
        ("authority_registration_after_security", "load_physical_controls", "assert_authority_registration"),
        ("current_session_body_registration", "load_body_registration_controls", "assert_body_registration"),
        ("current_session_group_union", "load_current_group_union_controls", "assert_current_group_union"),
        ("current_session_group_ledger_closeout", "load_current_group_ledger_closeout_controls", "assert_current_group_ledger_closeout"),
        ("post_security_five_registration", "load_post_security_five_registration_controls", "assert_post_security_five_registration"),
        ("post_security_five_ledger_closeout", "load_post_security_five_closeout_controls", "assert_post_security_five_closeout"),
    )
    for owner, loader, checker in calls:
        module = modules[owner]
        args = getattr(module, loader)()
        before = fingerprint(args)
        getattr(module, checker)(*args)
        assert fingerprint(args) == before, owner + " mutated supplied arguments"


def add_real_extension(root):
    source = (root / BEHAVIOR).read_bytes()
    assert len(source) == 18621 and hashlib.sha256(source).hexdigest() == BEHAVIOR_SHA
    record = {
        "path": BEHAVIOR, "sha256": BEHAVIOR_SHA, "utf8_bytes": len(source),
        "mode": "100644", "last_commit": "3ac62a6396f92d5c4d330b76843ca88cfdcb4ae0",
        "parent": "cd5e42f2a61a115e79ad855514de74aaf88d0843",
        "test_selectors": list(SELECTORS), "seed_ids": list(SEEDS),
    }
    packet = {"schema_version": 1, "stage": "P1-00b", "records": [record]}
    write_json(root, NEW_PACKET, packet)
    manifest = read_json(root, MANIFEST)
    manifest["evidence_sources"][NEW_KEY] = NEW_PACKET
    manifest["counts"] = dict(NEW_COUNTS)
    write_json(root, MANIFEST, manifest)
    return manifest


def test_unchanged_398_preserves_all_seven_public_owners(tree):
    root, history, modules = tree
    manifest = read_json(root, MANIFEST)
    before = encoded(manifest)
    surface, baseline, evidenced = history.assert_live_membership(manifest)
    assert (len(surface), len(baseline), len(evidenced)) == (465, 135, 398)
    assert history.historical_manifest(manifest)["counts"] == OLD_COUNTS
    assert_all_owners(modules)
    assert encoded(manifest) == before


def test_real_two_seed_growth_and_later_tail_preserve_historical_proofs(tree):
    root, history, modules = tree
    old_manifest = read_json(root, MANIFEST)
    _, old_baseline, old_evidenced = history.assert_live_membership(old_manifest)
    manifest = add_real_extension(root)
    surface, baseline, evidenced = history.assert_live_membership(manifest)
    assert baseline == old_baseline and old_evidenced <= evidenced
    assert evidenced - old_evidenced == set(SEEDS)
    assert (len(surface), len(baseline), len(evidenced), len(surface - evidenced)) == (465, 135, 400, 65)
    assert history.historical_manifest(manifest)["counts"] == OLD_COUNTS
    assert_all_owners(modules)
    manifest["evidence_sources"]["future_surface_api_canary_path"] = INVENTORY + "surface-api.json"
    write_json(root, MANIFEST, manifest)
    old_ledger = (root / LEDGER).read_bytes()
    with (root / LEDGER).open("ab") as stream:
        stream.write(b"\n### Legitimate later bounded section\n\n- No changes to historical owners.\n")
    assert (root / LEDGER).read_bytes().startswith(old_ledger)
    assert_all_owners(modules)
    assert history.assert_live_membership(manifest)[2] == evidenced


@pytest.mark.parametrize("bad", [True, 398.0, 399, 400])
def test_unbacked_or_noninteger_live_counts_reject(tree, bad):
    root, history, _ = tree
    manifest = read_json(root, MANIFEST)
    manifest["counts"]["evidenced_seed_count"] = bad
    with pytest.raises(AssertionError):
        history.assert_live_membership(manifest)
    with pytest.raises(AssertionError):
        history.historical_manifest(manifest)


def test_unknown_seed_is_not_accepted_by_adjusting_counts(tree):
    root, history, _ = tree
    manifest = add_real_extension(root)
    packet = read_json(root, NEW_PACKET)
    packet["records"][0]["seed_ids"][1] = "API-UNKNOWN-HISTORY-FIX-L999999"
    write_json(root, NEW_PACKET, packet)
    with pytest.raises(AssertionError):
        history.assert_live_membership(manifest)


def test_snapshot_rejects_missing_final_historical_mapping_with_unchanged_counts(tree, monkeypatch):
    root, history, modules = tree
    snapshot = modules["canonical_membership_snapshot"]
    manifest = read_json(root, MANIFEST)
    assert len(manifest["evidence_sources"]) == 33
    manifest["evidence_sources"].popitem()
    assert manifest["counts"] == OLD_COUNTS
    # This shorter exact prefix is legitimate only for nested historical
    # assertions. The physical snapshot loader must still require all33.
    assert len(history.historical_manifest(manifest)["evidence_sources"]) == 32
    original = snapshot._READER._json
    monkeypatch.setattr(snapshot._READER, "_json",
                        lambda rel: copy.deepcopy(manifest) if rel == MANIFEST else original(rel))
    with pytest.raises(AssertionError, match="mapping removed"):
        snapshot.make_canonical_membership_snapshot()


def test_removing_historical_member_rejects_even_with_recomputed_counts(tree):
    root, history, _ = tree
    manifest = read_json(root, MANIFEST)
    surface, baseline, evidenced = history.assert_live_membership(manifest)
    victim = sorted(evidenced - baseline)[0]
    for rel in manifest["evidence_sources"].values():
        document = read_json(root, rel)
        changed = False
        for record in document.get("records", []):
            if victim in record.get("seed_ids", []):
                record["seed_ids"] = [seed for seed in record["seed_ids"] if seed != victim]
                changed = True
        if changed:
            write_json(root, rel, document)
    manifest["counts"].update(reconciled_new_seed_count=262,
                              evidenced_seed_count=397, remaining_seed_count=68)
    with pytest.raises(AssertionError):
        history.assert_live_membership(manifest)


@pytest.mark.parametrize("mutation", ["path", "order", "root", "blocker"])
def test_frozen_manifest_structure_and_open_gates_cannot_be_projected_away(tree, mutation):
    root, history, _ = tree
    manifest = read_json(root, MANIFEST)
    if mutation == "path":
        manifest["evidence_sources"]["base_path"] = INVENTORY + "surface-api.json"
    elif mutation == "order":
        items = list(manifest["evidence_sources"].items())
        items[1], items[2] = items[2], items[1]
        manifest["evidence_sources"] = dict(items)
    elif mutation == "root":
        manifest["inventory"]["expected_seed_count"] = 466
    else:
        manifest["canonical_blockers"].pop()
    with pytest.raises(AssertionError):
        history.historical_manifest(manifest)


def test_original_fixture_sha_drift_rejects_not_new_live_module_pin(tree):
    root, history, _ = tree
    relative = "tests/test_p1_00b_characterization_canonical_membership_snapshot.py"
    expected = "7186a5eb551f50a8b35b6eceec38f896b58e7340cc0c5d6ac80086624d217deb"
    old = history.read_pin(relative, expected, 11785, root=root)
    assert len(old) == 11785 and hashlib.sha256(old).hexdigest() == expected
    fixture = root / "tests/fixtures/p1_00b_membership_history/blobs" / (expected + ".txt")
    raw = fixture.read_bytes()
    fixture.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    with pytest.raises(AssertionError):
        history.read_pin(relative, expected, 11785, root=root)


def test_unallowlisted_real_source_pin_still_reads_current_physical_bytes(tree):
    root, history, _ = tree
    original = (root / BEHAVIOR).read_bytes()
    assert history.read_pin(BEHAVIOR, BEHAVIOR_SHA, len(original), root=root) == original
    (root / BEHAVIOR).write_bytes(original + b"\n")
    with pytest.raises(AssertionError):
        history.read_pin(BEHAVIOR, BEHAVIOR_SHA, len(original), root=root)


def test_every_historical_fixture_uses_existing_native_source_suffix():
    from scripts.two_site_agent_sandbox import SOURCE_SUFFIXES
    fixtures = CANDIDATE / "tests/fixtures/p1_00b_membership_history"
    paths = [path for path in fixtures.rglob("*") if path.is_file()]
    assert paths and any(path.name == "index.json" for path in paths)
    assert all(path.suffix in SOURCE_SUFFIXES for path in paths)
    assert all(path.suffix != ".py" for path in paths), "archive is data, never executable Source"
