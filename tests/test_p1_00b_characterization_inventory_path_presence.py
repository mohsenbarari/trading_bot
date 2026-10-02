"""Characterization tests for inventory record 'path' key presence.

Prerequisite Context:
- Governed by P1-00b under Assignment WA-P1-00B-AFTER-OBSERVER-PROJECTION-20261002-161.
- Contributes test-only prerequisite evidence for open canonical blocker
  P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.
- Stage ledger status: P1-00 COMPLETE, P1-00b IN_PROGRESS.
- Governing reconciliation: reconciliation-17 (reconciliation=3/5, pending-five=5,
  units_since_reconciliation=5, evidenced_seed_count=387/465, remaining=78,
  origin_matrix_unresolved_cells=24).

Scenario:
- P1-00B-INVENTORY-RECORD-PATH-KEY-MISSING-V1: Characterizes
  tests/test_p1_00b_characterization_inventory_evidence_helper.py:assert_record_evidence
  rejecting a standalone JSON-decoded C15 record missing its required 'path' key.

Explicit Exclusions:
- Does not accept seed handlers, batches, or sixth behavior.
- Excludes read_user self-success and super-admin rejection.
- Excludes authorization policy coverage, mutation, persistence, retry, reload,
  origin matrix cell resolution, seed-blocker closure, or Stage closure.
- Does not repeat missing-sha256, missing-test_selectors, null/string,
  selector-domain/resolution, ownership, or hash-mismatch scenarios.
- Pure stdlib only: does not import or execute application, HTTP, database, or
  custom adapter code.
"""

import hashlib
import json
from pathlib import Path
import re
import unittest

from tests.test_p1_00b_characterization_inventory_evidence_helper import (
    assert_record_evidence,
)

# Independently frozen literal C15 inventory oracle
EXPECTED_C15_INVENTORY_ORACLE = {
    "schema_version": 1,
    "stage": "P1-00b",
    "predecessor_inventory": {
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json",
        "sha256": "782407b237098640fffa0ce1c011d80406ce0b1707b9b06428a5e911e0487554",
        "last_commit": "540dc1cba9e2efed477cd81b71b946a69b47fcde",
    },
    "records": [
        {
            "path": "tests/test_p1_00b_characterization_users_read_all_empty_http.py",
            "sha256": "83151fbafaa7953fbad50136ad564a0bdd2f5d39c1e5943c75a40b3364cf7faa",
            "last_commit": "f564bf674a6c5f7ab38be28a2ad92f69ff886ab8",
            "seed_ids": [
                "API-IDENTITY-GET-USERS-READ-ALL-USERS-L407",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_users_read_all_empty_http.py::UsersReadAllEmptyHttpCharacterizationTests::test_empty_list_returns_http_200_without_relation_queries",
            ],
        },
    ],
}

# Independently frozen SHA256 digests
EXPECTED_INVENTORY_SHA256 = (
    "11bc150e1d9e1e92fd317b90078a38b6e28819d7f8b8f812ad19399dc91369b5"
)
EXPECTED_SOURCE_SHA256 = (
    "83151fbafaa7953fbad50136ad564a0bdd2f5d39c1e5943c75a40b3364cf7faa"
)
EXPECTED_HELPER_SHA256 = (
    "522af0f05ca6d83c19d2f36581d53f7cc85a5d27d10dff4b00a290d4e2e88c4c"
)

# Standalone literal JSON record lacking only 'path'
STANDALONE_MALFORMED_RECORD_JSON = (
    "{\n"
    '  "sha256": "83151fbafaa7953fbad50136ad564a0bdd2f5d39c1e5943c75a40b3364cf7faa",\n'
    '  "last_commit": "f564bf674a6c5f7ab38be28a2ad92f69ff886ab8",\n'
    '  "seed_ids": [\n'
    '    "API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"\n'
    "  ],\n"
    '  "test_selectors": [\n'
    '    "tests/test_p1_00b_characterization_users_read_all_empty_http.py::UsersReadAllEmptyHttpCharacterizationTests::test_empty_list_returns_http_200_without_relation_queries"\n'
    "  ]\n"
    "}\n"
)


class InventoryPathPresenceTests(unittest.TestCase):
    """Characterization tests verifying required 'path' key enforcement in inventory records."""

    def test_missing_path_key_rejected(self) -> None:
        # Derive repository root without assumption of current working directory
        repository_root = Path(__file__).resolve().parent.parent

        inventory_path = (
            repository_root
            / "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json"
        )
        source_path = (
            repository_root
            / "tests/test_p1_00b_characterization_users_read_all_empty_http.py"
        )
        helper_path = (
            repository_root
            / "tests/test_p1_00b_characterization_inventory_evidence_helper.py"
        )

        self.assertTrue(inventory_path.is_file(), f"Missing inventory file: {inventory_path}")
        self.assertTrue(source_path.is_file(), f"Missing source file: {source_path}")
        self.assertTrue(helper_path.is_file(), f"Missing helper file: {helper_path}")

        # --- Requirement 1: Baseline Verification of Genuine Artifacts ---
        pre_inventory_bytes = inventory_path.read_bytes()
        pre_source_bytes = source_path.read_bytes()
        pre_helper_bytes = helper_path.read_bytes()

        self.assertEqual(
            hashlib.sha256(pre_inventory_bytes).hexdigest(),
            EXPECTED_INVENTORY_SHA256,
            "Inventory SHA256 mismatch against frozen digest",
        )
        self.assertEqual(
            hashlib.sha256(pre_source_bytes).hexdigest(),
            EXPECTED_SOURCE_SHA256,
            "Evidence source SHA256 mismatch against frozen digest",
        )
        self.assertEqual(
            hashlib.sha256(pre_helper_bytes).hexdigest(),
            EXPECTED_HELPER_SHA256,
            "Helper SHA256 mismatch against frozen digest",
        )

        genuine_inventory = json.loads(pre_inventory_bytes.decode("utf-8"))
        self.assertEqual(genuine_inventory, EXPECTED_C15_INVENTORY_ORACLE)
        self.assertIs(type(genuine_inventory["schema_version"]), int)
        self.assertEqual(genuine_inventory["schema_version"], 1)
        self.assertEqual(genuine_inventory["stage"], "P1-00b")
        self.assertEqual(
            genuine_inventory["predecessor_inventory"],
            EXPECTED_C15_INVENTORY_ORACLE["predecessor_inventory"],
        )

        self.assertEqual(len(genuine_inventory["records"]), 1)
        genuine_record = genuine_inventory["records"][0]
        self.assertIsInstance(genuine_record, dict)
        self.assertEqual(
            genuine_record["path"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["path"],
        )
        self.assertEqual(
            genuine_record["sha256"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["sha256"],
        )
        self.assertEqual(
            genuine_record["last_commit"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["last_commit"],
        )
        self.assertEqual(
            genuine_record["seed_ids"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["seed_ids"],
        )
        self.assertEqual(
            genuine_record["test_selectors"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["test_selectors"],
        )

        # Exercise assert_record_evidence with genuine record prior to rejection
        assert_record_evidence(self, genuine_record, repository_root)

        # --- Requirement 2: Malformed Record Construction & Narrow Rejection ---
        malformed_record = json.loads(STANDALONE_MALFORMED_RECORD_JSON)
        self.assertIsInstance(malformed_record, dict)
        self.assertNotIn("path", malformed_record)
        self.assertEqual(
            set(genuine_record.keys()) - set(malformed_record.keys()),
            {"path"},
        )
        self.assertEqual(
            set(malformed_record.keys()) - set(genuine_record.keys()),
            set(),
        )
        self.assertEqual(
            set(malformed_record.keys()),
            {"sha256", "last_commit", "seed_ids", "test_selectors"},
        )
        self.assertEqual(
            malformed_record["sha256"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["sha256"],
        )
        self.assertEqual(
            malformed_record["last_commit"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["last_commit"],
        )
        self.assertEqual(
            malformed_record["seed_ids"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["seed_ids"],
        )
        self.assertEqual(
            malformed_record["test_selectors"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["test_selectors"],
        )

        # Prove object and list identity independence from genuine record
        self.assertIsNot(malformed_record, genuine_record)
        self.assertIsNot(malformed_record["seed_ids"], genuine_record["seed_ids"])
        self.assertIsNot(
            malformed_record["test_selectors"],
            genuine_record["test_selectors"],
        )

        # Narrow rejection block: only the single helper call
        with self.assertRaisesRegex(AssertionError, re.escape("'path' not found in")):
            assert_record_evidence(self, malformed_record, repository_root)

        # --- Requirement 3: Post-Rejection Invariance & Validation ---
        # Verify malformed object remains unchanged and still lacks only path
        self.assertNotIn("path", malformed_record)
        self.assertEqual(
            set(malformed_record.keys()),
            {"sha256", "last_commit", "seed_ids", "test_selectors"},
        )
        self.assertEqual(
            set(genuine_record.keys()) - set(malformed_record.keys()),
            {"path"},
        )
        self.assertEqual(
            set(malformed_record.keys()) - set(genuine_record.keys()),
            set(),
        )
        self.assertEqual(
            malformed_record["sha256"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["sha256"],
        )
        self.assertEqual(
            malformed_record["last_commit"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["last_commit"],
        )
        self.assertEqual(
            malformed_record["seed_ids"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["seed_ids"],
        )
        self.assertEqual(
            malformed_record["test_selectors"],
            EXPECTED_C15_INVENTORY_ORACLE["records"][0]["test_selectors"],
        )

        # Genuine record still equals its complete independent oracle and passes helper
        self.assertEqual(
            genuine_record,
            EXPECTED_C15_INVENTORY_ORACLE["records"][0],
        )
        assert_record_evidence(self, genuine_record, repository_root)

        # All raw bytes equal pre-rejection snapshots and literal digests
        post_inventory_bytes = inventory_path.read_bytes()
        post_source_bytes = source_path.read_bytes()
        post_helper_bytes = helper_path.read_bytes()

        self.assertEqual(post_inventory_bytes, pre_inventory_bytes)
        self.assertEqual(post_source_bytes, pre_source_bytes)
        self.assertEqual(post_helper_bytes, pre_helper_bytes)

        self.assertEqual(
            hashlib.sha256(post_inventory_bytes).hexdigest(),
            EXPECTED_INVENTORY_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(post_source_bytes).hexdigest(),
            EXPECTED_SOURCE_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(post_helper_bytes).hexdigest(),
            EXPECTED_HELPER_SHA256,
        )

        # Re-decode inventory bytes, prove full equality, and re-validate with helper
        reloaded_inventory = json.loads(post_inventory_bytes.decode("utf-8"))
        self.assertEqual(reloaded_inventory, EXPECTED_C15_INVENTORY_ORACLE)
        self.assertEqual(reloaded_inventory["records"][0], genuine_record)
        assert_record_evidence(
            self,
            reloaded_inventory["records"][0],
            repository_root,
        )
