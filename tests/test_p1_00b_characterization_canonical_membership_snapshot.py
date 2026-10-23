"""Canonical membership snapshot and independently derived counts oracle.

Stdlib and accepted accounting-helper module reuse only; no application, database,
network, Git, or Provider execution. Proves raw canonical membership sets and
set-difference counts only, without claiming authority record validation,
reconciliation credit, or strict 288/177 closure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from unittest import mock

import tests.test_p1_00b_characterization_notification_mark_all_accounting as accounting_module

SURFACE_SEED_COUNT = 465
BASELINE_SEED_COUNT = 135
RECONCILED_NEW_SEED_COUNT = 263
EVIDENCED_SEED_COUNT = 398
REMAINING_SEED_COUNT = 67

SURFACE_SET_SHA256 = "f3fed11dff487348bc998306b4eb8c17657e00d81c794bb09a1b3cbdc3f28d91"
BASELINE_SET_SHA256 = "6a8018ea4a8ee0366cff417bd8f008658c95d9ee9f9d0bc759beafb029161648"
EVIDENCED_SET_SHA256 = "a7e5c29f5075d59c357a369d8a403ff74d5a357debbdbd7a4590c15a1d017ff7"

AUTHORITY_SEED_ID = "API-AUTH-POST-SESSIONS-INTERNAL-SESSION-AUTHORITY-CHECK-L539"

SNAPSHOT_KEYS = (
    "surface_seed_ids",
    "baseline_seed_ids",
    "evidenced_seed_ids",
    "counts",
)
COUNT_KEYS = (
    "baseline_evidenced_seed_count",
    "reconciled_new_seed_count",
    "evidenced_seed_count",
    "remaining_seed_count",
)
EXPECTED_COUNTS = {
    "baseline_evidenced_seed_count": BASELINE_SEED_COUNT,
    "reconciled_new_seed_count": RECONCILED_NEW_SEED_COUNT,
    "evidenced_seed_count": EVIDENCED_SEED_COUNT,
    "remaining_seed_count": REMAINING_SEED_COUNT,
}

_READER = accounting_module.NotificationMarkAllAccountingTests(methodName="runTest")


def _canonical_set_digest(values: list[str] | set[str]) -> str:
    payload = json.dumps(
        sorted(values), ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _derive_counts_from_sets(
    surface: set[str], baseline: set[str], evidenced: set[str]
) -> dict[str, int]:
    if not (baseline <= evidenced <= surface):
        raise AssertionError("Canonical seed sets must satisfy baseline <= evidenced <= surface")
    return {
        "baseline_evidenced_seed_count": len(baseline),
        "reconciled_new_seed_count": len(evidenced - baseline),
        "evidenced_seed_count": len(evidenced),
        "remaining_seed_count": len(surface - evidenced),
    }


def _assert_exact_int_counts_mapping(counts: object) -> dict[str, int]:
    if type(counts) is not dict:
        raise AssertionError("counts must be a dict")
    if set(counts.keys()) != set(COUNT_KEYS) or len(counts) != len(COUNT_KEYS):
        raise AssertionError("counts must contain the exact four canonical count keys")
    for key in COUNT_KEYS:
        value = counts[key]
        if type(value) is not int:
            raise AssertionError(f"counts[{key!r}] must have exact type int")
    return dict(counts)


def _assert_seed_id_list(values: object, label: str) -> list[str]:
    if type(values) is not list:
        raise AssertionError(f"{label} must be a list")
    if not all(type(item) is str and bool(item) for item in values):
        raise AssertionError(f"{label} elements must be non-empty str instances")
    if len(values) != len(set(values)) or values != sorted(values):
        raise AssertionError(f"{label} must be strictly sorted and unique")
    return list(values)


def assert_canonical_membership_snapshot(snapshot: dict) -> None:
    if type(snapshot) is not dict:
        raise AssertionError("snapshot must be a dict")
    if set(snapshot.keys()) != set(SNAPSHOT_KEYS) or len(snapshot) != len(SNAPSHOT_KEYS):
        raise AssertionError("snapshot must have the exact four required keys")

    surface_ids = _assert_seed_id_list(snapshot["surface_seed_ids"], "surface_seed_ids")
    baseline_ids = _assert_seed_id_list(snapshot["baseline_seed_ids"], "baseline_seed_ids")
    evidenced_ids = _assert_seed_id_list(snapshot["evidenced_seed_ids"], "evidenced_seed_ids")
    counts = _assert_exact_int_counts_mapping(snapshot["counts"])

    surface_set = set(surface_ids)
    baseline_set = set(baseline_ids)
    evidenced_set = set(evidenced_ids)

    if not (baseline_set <= evidenced_set <= surface_set):
        raise AssertionError("snapshot sets violate baseline <= evidenced <= surface")

    if len(surface_ids) != SURFACE_SEED_COUNT:
        raise AssertionError(f"surface_seed_ids count {len(surface_ids)} != {SURFACE_SEED_COUNT}")
    if len(baseline_ids) != BASELINE_SEED_COUNT:
        raise AssertionError(f"baseline_seed_ids count {len(baseline_ids)} != {BASELINE_SEED_COUNT}")
    if len(evidenced_ids) != EVIDENCED_SEED_COUNT:
        raise AssertionError(f"evidenced_seed_ids count {len(evidenced_ids)} != {EVIDENCED_SEED_COUNT}")

    if _canonical_set_digest(surface_ids) != SURFACE_SET_SHA256:
        raise AssertionError("surface_seed_ids digest mismatch")
    if _canonical_set_digest(baseline_ids) != BASELINE_SET_SHA256:
        raise AssertionError("baseline_seed_ids digest mismatch")
    if _canonical_set_digest(evidenced_ids) != EVIDENCED_SET_SHA256:
        raise AssertionError("evidenced_seed_ids digest mismatch")

    if AUTHORITY_SEED_ID not in surface_set:
        raise AssertionError("authority seed missing from surface_seed_ids")
    if AUTHORITY_SEED_ID not in evidenced_set:
        raise AssertionError("authority seed missing from evidenced_seed_ids")
    if AUTHORITY_SEED_ID in baseline_set:
        raise AssertionError("authority seed must be absent from baseline_seed_ids")

    derived_counts = _derive_counts_from_sets(surface_set, baseline_set, evidenced_set)
    if counts != derived_counts or counts != EXPECTED_COUNTS:
        raise AssertionError("snapshot counts do not match independently derived counts")


def make_canonical_membership_snapshot() -> dict:
    manifest = _READER._json(accounting_module.MANIFEST)
    receipt = _READER._json(accounting_module.RECEIPT)
    if type(manifest) is not dict or type(receipt) is not dict:
        raise AssertionError("manifest and receipt must be JSON objects")

    manifest_counts = _assert_exact_int_counts_mapping(manifest.get("counts"))
    surface, baseline, evidenced = _READER._derive(manifest, receipt)
    derived_counts = _derive_counts_from_sets(surface, baseline, evidenced)
    if manifest_counts != derived_counts:
        raise AssertionError(
            "manifest declared counts do not match independently derived set counts"
        )

    snapshot = {
        "surface_seed_ids": sorted(surface),
        "baseline_seed_ids": sorted(baseline),
        "evidenced_seed_ids": sorted(evidenced),
        "counts": dict(derived_counts),
    }
    assert_canonical_membership_snapshot(snapshot)
    return snapshot


class CanonicalMembershipSnapshotTests(unittest.TestCase):
    def test_physical_membership_snapshot_matches_independent_counts(self) -> None:
        snapshot = make_canonical_membership_snapshot()
        assert_canonical_membership_snapshot(snapshot)

        self.assertEqual(set(snapshot.keys()), set(SNAPSHOT_KEYS))
        self.assertEqual(
            (
                len(snapshot["surface_seed_ids"]),
                len(snapshot["baseline_seed_ids"]),
                len(snapshot["evidenced_seed_ids"]),
            ),
            (465, 135, 398),
        )
        self.assertEqual(
            _canonical_set_digest(snapshot["surface_seed_ids"]),
            SURFACE_SET_SHA256,
        )
        self.assertEqual(
            _canonical_set_digest(snapshot["baseline_seed_ids"]),
            BASELINE_SET_SHA256,
        )
        self.assertEqual(
            _canonical_set_digest(snapshot["evidenced_seed_ids"]),
            EVIDENCED_SET_SHA256,
        )
        self.assertEqual(snapshot["counts"], EXPECTED_COUNTS)
        self.assertTrue(all(type(v) is int for v in snapshot["counts"].values()))

        surface_set = set(snapshot["surface_seed_ids"])
        baseline_set = set(snapshot["baseline_seed_ids"])
        evidenced_set = set(snapshot["evidenced_seed_ids"])
        self.assertTrue(baseline_set <= evidenced_set <= surface_set)
        self.assertEqual(len(evidenced_set - baseline_set), 263)
        self.assertEqual(len(surface_set - evidenced_set), 67)

        self.assertIn(AUTHORITY_SEED_ID, surface_set)
        self.assertIn(AUTHORITY_SEED_ID, evidenced_set)
        self.assertNotIn(AUTHORITY_SEED_ID, baseline_set)
        self.assertIn(AUTHORITY_SEED_ID, evidenced_set - baseline_set)

        manifest = _READER._json(accounting_module.MANIFEST)
        continuation = _READER._json(manifest["evidence_sources"]["continuation_path"])
        self.assertIn(AUTHORITY_SEED_ID, continuation["records"][32]["seed_ids"])

        snapshot["surface_seed_ids"].append("MUTATED-SURFACE-SEED")
        snapshot["baseline_seed_ids"].pop()
        snapshot["evidenced_seed_ids"].clear()
        snapshot["counts"]["evidenced_seed_count"] = 0
        with self.assertRaises(AssertionError):
            assert_canonical_membership_snapshot(snapshot)

        fresh_snapshot = make_canonical_membership_snapshot()
        assert_canonical_membership_snapshot(fresh_snapshot)
        self.assertIsNot(fresh_snapshot, snapshot)
        self.assertIsNot(fresh_snapshot["surface_seed_ids"], snapshot["surface_seed_ids"])
        self.assertIsNot(fresh_snapshot["baseline_seed_ids"], snapshot["baseline_seed_ids"])
        self.assertIsNot(fresh_snapshot["evidenced_seed_ids"], snapshot["evidenced_seed_ids"])
        self.assertIsNot(fresh_snapshot["counts"], snapshot["counts"])
        self.assertEqual(fresh_snapshot["counts"], EXPECTED_COUNTS)

    def test_membership_and_declared_count_drift_reject(self) -> None:
        healthy = make_canonical_membership_snapshot()
        assert_canonical_membership_snapshot(healthy)

        corrupted_count = copy.deepcopy(healthy)
        corrupted_count["counts"]["evidenced_seed_count"] = 399
        with self.assertRaises(AssertionError):
            assert_canonical_membership_snapshot(corrupted_count)
        assert_canonical_membership_snapshot(healthy)
        assert_canonical_membership_snapshot(make_canonical_membership_snapshot())

        swapped_baseline = copy.deepcopy(healthy)
        replaced_baseline_seed = swapped_baseline["baseline_seed_ids"][0]
        self.assertNotEqual(replaced_baseline_seed, AUTHORITY_SEED_ID)
        swapped_set = (set(swapped_baseline["baseline_seed_ids"]) - {replaced_baseline_seed}) | {
            AUTHORITY_SEED_ID
        }
        swapped_baseline["baseline_seed_ids"] = sorted(swapped_set)
        self.assertEqual(len(swapped_baseline["baseline_seed_ids"]), 135)
        self.assertTrue(
            set(swapped_baseline["baseline_seed_ids"])
            <= set(swapped_baseline["evidenced_seed_ids"])
        )
        with self.assertRaises(AssertionError):
            assert_canonical_membership_snapshot(swapped_baseline)
        assert_canonical_membership_snapshot(healthy)
        assert_canonical_membership_snapshot(make_canonical_membership_snapshot())

        original_json = _READER._json

        def _manifest_count_drift_json(relative: str) -> dict:
            document = original_json(relative)
            if relative == accounting_module.MANIFEST:
                drifted = copy.deepcopy(document)
                drifted["counts"]["evidenced_seed_count"] = 399
                return drifted
            return document

        with mock.patch.object(_READER, "_json", side_effect=_manifest_count_drift_json):
            with self.assertRaises(AssertionError):
                make_canonical_membership_snapshot()

        restored = make_canonical_membership_snapshot()
        assert_canonical_membership_snapshot(restored)
        self.assertEqual(restored, healthy)
