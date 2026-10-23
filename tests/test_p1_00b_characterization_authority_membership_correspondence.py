"""Fixed authority-record and canonical-membership correspondence witness.

Stage: P1-00b (docs/refactor/two-server/section-1/stages/P1-00b.md)
Assignment: WA-P1-00B-BUSINESS-LIVE-20261023-803B
Parent assignment: WA-P1-00B-FREEZE-20260904-001

Correspondence prerequisite only. Uses stdlib and module imports of the two
accepted prerequisite oracles without importing or executing the live authority
characterization test, application models, database sessions, network, Git, or
Provider APIs. Claims zero behavior, seed, origin, Stage, or reconciliation
credit and registers no canonical packet, historical acceptance, ledger
integration, or strict 288/177 closure.
"""

from __future__ import annotations

import copy
from typing import Any
import unittest

import tests.test_p1_00b_characterization_authority_record_contract_oracle as record_oracle
import tests.test_p1_00b_characterization_canonical_membership_snapshot as snapshot_oracle

AUTHORITY_SEED_ID = (
    "API-AUTH-POST-SESSIONS-INTERNAL-SESSION-AUTHORITY-CHECK-L539"
)
EXPECTED_SOURCE_COMMIT = "f4dd10018ef832881894e276bfa214e070ce7422"
EXPECTED_SOURCE_SHA256 = (
    "d7a60d5f329d0889a2fcba3d52b28cb0b41c72c51c913e278f3bfdf3637903d1"
)

WITNESS_KEYS = (
    "authority_record",
    "canonical_snapshot",
    "raw_membership_delta",
    "counts_after_union",
)
COUNT_KEYS = (
    "baseline_evidenced_seed_count",
    "reconciled_new_seed_count",
    "evidenced_seed_count",
    "remaining_seed_count",
)
EXPECTED_COUNTS_AFTER_UNION = {
    "baseline_evidenced_seed_count": 135,
    "reconciled_new_seed_count": 263,
    "evidenced_seed_count": 398,
    "remaining_seed_count": 67,
}


def _assert_exact_str_list(value: Any, label: str) -> list[str]:
    if type(value) is not list:
        raise AssertionError(f"{label} must have exact type list")
    for item in value:
        if type(item) is not str or not item:
            raise AssertionError(f"{label} items must be non-empty str")
    if len(value) != len(set(value)) or value != sorted(value):
        raise AssertionError(f"{label} must be sorted and unique")
    return list(value)


def _assert_exact_counts(counts: Any, label: str) -> dict[str, int]:
    if type(counts) is not dict:
        raise AssertionError(f"{label} must have exact type dict")
    if set(counts.keys()) != set(COUNT_KEYS) or len(counts) != len(COUNT_KEYS):
        raise AssertionError(f"{label} must have exact keys {COUNT_KEYS!r}")
    for key in COUNT_KEYS:
        if type(key) is not str:
            raise AssertionError(f"{label} keys must have exact type str")
        val = counts[key]
        if type(val) is not int:
            raise AssertionError(f"{label}[{key!r}] must have exact type int")
    return dict(counts)


def _derive_correspondence(
    record: dict[str, Any],
    snapshot: dict[str, Any],
) -> tuple[list[str], dict[str, int]]:
    seed_ids = _assert_exact_str_list(record.get("seed_ids"), "record.seed_ids")
    if seed_ids != [AUTHORITY_SEED_ID]:
        raise AssertionError(
            f"record.seed_ids must be singleton {[AUTHORITY_SEED_ID]!r}"
        )
    singleton_seed = seed_ids[0]

    surface = set(snapshot["surface_seed_ids"])
    baseline = set(snapshot["baseline_seed_ids"])
    evidenced = set(snapshot["evidenced_seed_ids"])

    if singleton_seed not in surface:
        raise AssertionError("singleton authority seed missing from surface")
    if singleton_seed not in evidenced:
        raise AssertionError("singleton authority seed missing from evidenced")
    if singleton_seed in baseline:
        raise AssertionError("singleton authority seed must not be in baseline")

    union_evidenced = evidenced | set(record["seed_ids"])
    if not (baseline <= union_evidenced <= surface):
        raise AssertionError("union violates baseline <= union_evidenced <= surface")

    raw_delta = sorted((evidenced | set(record["seed_ids"])) - evidenced)
    counts_after_union = {
        "baseline_evidenced_seed_count": len(baseline),
        "reconciled_new_seed_count": len(union_evidenced - baseline),
        "evidenced_seed_count": len(union_evidenced),
        "remaining_seed_count": len(surface - union_evidenced),
    }
    return raw_delta, counts_after_union


def assert_authority_membership_correspondence(witness: dict) -> None:
    """Validate authority record, canonical snapshot, and zero-delta union."""
    if type(witness) is not dict:
        raise AssertionError("witness must have exact type dict")
    if set(witness.keys()) != set(WITNESS_KEYS) or len(witness) != len(WITNESS_KEYS):
        raise AssertionError(f"witness must have exact keys {WITNESS_KEYS!r}")
    for key in witness.keys():
        if type(key) is not str:
            raise AssertionError("witness keys must have exact type str")

    record = witness["authority_record"]
    snapshot = witness["canonical_snapshot"]
    record_oracle.assert_authority_record(record)
    snapshot_oracle.assert_canonical_membership_snapshot(snapshot)

    supplied_delta = _assert_exact_str_list(
        witness["raw_membership_delta"],
        "raw_membership_delta",
    )
    supplied_counts = _assert_exact_counts(
        witness["counts_after_union"],
        "counts_after_union",
    )

    derived_delta, derived_counts = _derive_correspondence(record, snapshot)
    if supplied_delta != derived_delta or supplied_delta != []:
        raise AssertionError(
            f"raw_membership_delta {supplied_delta!r} != derived {derived_delta!r}"
        )
    if (
        supplied_counts != derived_counts
        or supplied_counts != EXPECTED_COUNTS_AFTER_UNION
        or supplied_counts != snapshot["counts"]
    ):
        raise AssertionError(
            f"counts_after_union {supplied_counts!r} != derived {derived_counts!r}"
        )


def make_authority_membership_correspondence() -> dict:
    """Build and validate a fresh correspondence witness from physical state."""
    record = copy.deepcopy(record_oracle.make_authority_record())
    snapshot = copy.deepcopy(snapshot_oracle.make_canonical_membership_snapshot())
    raw_delta, counts_after_union = _derive_correspondence(record, snapshot)
    witness = {
        "authority_record": record,
        "canonical_snapshot": snapshot,
        "raw_membership_delta": list(raw_delta),
        "counts_after_union": dict(counts_after_union),
    }
    assert_authority_membership_correspondence(witness)
    return copy.deepcopy(witness)


class AuthorityMembershipCorrespondenceTests(unittest.TestCase):
    def test_physical_record_and_snapshot_have_zero_raw_contribution(
        self,
    ) -> None:
        first = make_authority_membership_correspondence()
        second = make_authority_membership_correspondence()
        assert_authority_membership_correspondence(first)
        assert_authority_membership_correspondence(second)

        self.assertEqual(set(first.keys()), set(WITNESS_KEYS))
        record = first["authority_record"]
        snapshot = first["canonical_snapshot"]

        self.assertEqual(record["seed_ids"], [AUTHORITY_SEED_ID])
        self.assertEqual(record["last_commit"], EXPECTED_SOURCE_COMMIT)
        self.assertEqual(record["sha256"], EXPECTED_SOURCE_SHA256)
        self.assertIn(AUTHORITY_SEED_ID, snapshot["surface_seed_ids"])
        self.assertIn(AUTHORITY_SEED_ID, snapshot["evidenced_seed_ids"])
        self.assertNotIn(AUTHORITY_SEED_ID, snapshot["baseline_seed_ids"])
        self.assertEqual(first["raw_membership_delta"], [])
        self.assertEqual(
            first["counts_after_union"],
            EXPECTED_COUNTS_AFTER_UNION,
        )
        self.assertEqual(snapshot["counts"], EXPECTED_COUNTS_AFTER_UNION)
        self.assertTrue(
            all(type(v) is int for v in first["counts_after_union"].values())
        )

        credits = record["credits"]
        self.assertEqual(credits["added_behavior_units"], 0)
        self.assertEqual(credits["seed_credit"], 0)
        self.assertEqual(credits["origin_credit"], 0)
        self.assertEqual(credits["stage_credit"], 0)
        self.assertEqual(credits["reconciliation_credit"], 0)
        self.assertEqual(credits["reconciled_units"], 3)
        self.assertEqual(credits["group_total_units"], 5)
        self.assertEqual(credits["reconciled_scenarios"], 4)
        self.assertEqual(credits["group_total_scenarios"], 7)
        self.assertEqual(credits["pending_five"], 5)
        self.assertFalse(credits["canonical_packet_registered"])
        self.assertFalse(credits["claims_accounting_composition"])
        self.assertFalse(credits["claims_historical_git_or_provider_acceptance"])
        self.assertTrue(all(record["exclusions"].values()))

        self.assertIsNot(first, second)
        self.assertIsNot(first["authority_record"], second["authority_record"])
        self.assertIsNot(
            first["authority_record"]["characterized_contract"],
            second["authority_record"]["characterized_contract"],
        )
        self.assertIsNot(
            first["canonical_snapshot"],
            second["canonical_snapshot"],
        )
        self.assertIsNot(
            first["canonical_snapshot"]["evidenced_seed_ids"],
            second["canonical_snapshot"]["evidenced_seed_ids"],
        )
        self.assertIsNot(
            first["raw_membership_delta"],
            second["raw_membership_delta"],
        )
        self.assertIsNot(
            first["counts_after_union"],
            second["counts_after_union"],
        )

        first["authority_record"]["seed_ids"].append("MUTATED-SEED")
        first["canonical_snapshot"]["evidenced_seed_ids"].pop()
        first["raw_membership_delta"].append(AUTHORITY_SEED_ID)
        first["counts_after_union"]["evidenced_seed_count"] = 399
        with self.assertRaises(AssertionError):
            assert_authority_membership_correspondence(first)

        assert_authority_membership_correspondence(second)
        fresh = make_authority_membership_correspondence()
        assert_authority_membership_correspondence(fresh)
        self.assertEqual(fresh, second)

    def test_domain_or_count_overclaim_rejects_and_restores_physical_control(
        self,
    ) -> None:
        control = make_authority_membership_correspondence()
        assert_authority_membership_correspondence(control)

        wrong_symbol = make_authority_membership_correspondence()
        wrong_symbol["authority_record"]["canonical_symbol"] = (
            "api/routers/sessions.py:reset_session_authority"
        )
        with self.assertRaises(AssertionError):
            assert_authority_membership_correspondence(wrong_symbol)
        assert_authority_membership_correspondence(
            make_authority_membership_correspondence()
        )

        swapped_baseline = make_authority_membership_correspondence()
        baseline_ids = swapped_baseline["canonical_snapshot"]["baseline_seed_ids"]
        replaced_seed = baseline_ids[0]
        self.assertNotEqual(replaced_seed, AUTHORITY_SEED_ID)
        swapped_baseline["canonical_snapshot"]["baseline_seed_ids"] = sorted(
            (set(baseline_ids) - {replaced_seed}) | {AUTHORITY_SEED_ID}
        )
        with self.assertRaises(AssertionError):
            assert_authority_membership_correspondence(swapped_baseline)
        assert_authority_membership_correspondence(
            make_authority_membership_correspondence()
        )

        delta_overclaim = make_authority_membership_correspondence()
        delta_overclaim["raw_membership_delta"] = [AUTHORITY_SEED_ID]
        with self.assertRaises(AssertionError):
            assert_authority_membership_correspondence(delta_overclaim)
        assert_authority_membership_correspondence(
            make_authority_membership_correspondence()
        )

        counts_overclaim = make_authority_membership_correspondence()
        counts_overclaim["counts_after_union"].update(
            {
                "reconciled_new_seed_count": 264,
                "evidenced_seed_count": 399,
                "remaining_seed_count": 66,
            }
        )
        with self.assertRaises(AssertionError):
            assert_authority_membership_correspondence(counts_overclaim)

        restored = make_authority_membership_correspondence()
        assert_authority_membership_correspondence(restored)
        self.assertEqual(restored, control)


if __name__ == "__main__":
    unittest.main()
