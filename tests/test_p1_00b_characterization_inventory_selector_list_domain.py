"""Characterization tests for inventory test-selector list-container domain rejection in assert_record_evidence.

Stage: P1-00b (IN_PROGRESS)
Assignment: WA-P1-00B-AFTER-OBSERVER-PROJECTION-20261002-161 (SHA256 b2dbac223995215c207ba881b3eea2673bb6b428a7962cb9beb9562c69156883)
Parent: WA-P1-00B-FREEZE-20260904-001
Canonical Open Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Shared seed: API-IDENTITY-GET-USERS-READ-ALL-USERS-L407

Scenarios:
- Scenario 1 (test_null_test_selectors_rejected): Standalone C15 record with
  test_selectors=null rejected by assert_record_evidence with list-type
  AssertionError indicating None is not an instance of list.
- Scenario 2 (test_string_test_selectors_rejected): Standalone C15 record with
  test_selectors as the genuine selector string rejected by assert_record_evidence
  with list-type AssertionError indicating string is not an instance of list.

Exclusions:
- Excludes already accepted two-component and async-method scenarios at 315b3c6d4b781fbba3cc58f4598f2da0b06ca663.
- Excludes missing-class and missing-method scenarios at 06ad31a37619bd9cc650cbb7f42a6b001fc568ce.
- Excludes helper path and hash-mismatch scenarios at 73ef5f046fa99723467ae1b669e96631c61449ef.
- Excludes HTTP runtime, authentication, and authorization policy execution.
- Excludes read_user self-success and super-admin rejection.
- Excludes list-item type, empty list, selector format/path/resolution, predecessor ownership,
  and schema-domain mutation.
- Excludes mutation, persistence, retry, reload, origin matrix cells, ledger/inventory/checkpoint/count edits,
  sixth behavior acceptance, and any seed/blocker/Stage closure.

Prerequisite status:
- Distinct test-only helper-consumer prerequisite for P1-00b. Adds no accepted behavior/seed/cell
  and is not a seed-handler batch.
- Current accepted ledger and reconciliation-17 remain authoritative: reconciliation=3/5,
  pending-five=5, units_since_reconciliation=5; remaining C15 empty-list HTTP200 and C16
  manager-peer HTTP403 remain pending. Coverage remains 387/465 with 78 remaining; both High blockers
  open (P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE, P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN); 24 origin
  cells unresolved; P1-00 COMPLETE, P1-00b IN_PROGRESS.
"""

import hashlib
import json
from pathlib import Path
import re
import unittest

from tests.test_p1_00b_characterization_inventory_evidence_helper import (
    assert_record_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Authoritative literal C15 oracle constants (independently frozen, not imported from helper)
C15_INVENTORY_REL_PATH = Path(
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json"
)
C15_INVENTORY_SHA256 = (
    "11bc150e1d9e1e92fd317b90078a38b6e28819d7f8b8f812ad19399dc91369b5"
)
C15_RECORD_PATH = (
    "tests/test_p1_00b_characterization_users_read_all_empty_http.py"
)
C15_RECORD_SHA256 = (
    "83151fbafaa7953fbad50136ad564a0bdd2f5d39c1e5943c75a40b3364cf7faa"
)
C15_LAST_COMMIT = "f564bf674a6c5f7ab38be28a2ad92f69ff886ab8"
C15_SEED_IDS = ["API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"]
C15_GENUINE_SELECTOR = (
    "tests/test_p1_00b_characterization_users_read_all_empty_http.py"
    "::UsersReadAllEmptyHttpCharacterizationTests"
    "::test_empty_list_returns_http_200_without_relation_queries"
)

HELPER_REL_PATH = Path(
    "tests/test_p1_00b_characterization_inventory_evidence_helper.py"
)
HELPER_SHA256 = (
    "522af0f05ca6d83c19d2f36581d53f7cc85a5d27d10dff4b00a290d4e2e88c4c"
)


class InventorySelectorListDomainTests(unittest.TestCase):
    """Characterization tests for inventory test_selectors list domain rejection."""

    def _read_and_assert_genuine_oracle(self) -> tuple[bytes, bytes, bytes, dict]:
        inv_path = REPO_ROOT / C15_INVENTORY_REL_PATH
        src_path = REPO_ROOT / C15_RECORD_PATH
        hlp_path = REPO_ROOT / HELPER_REL_PATH

        inv_bytes = inv_path.read_bytes()
        src_bytes = src_path.read_bytes()
        hlp_bytes = hlp_path.read_bytes()

        self.assertEqual(hashlib.sha256(inv_bytes).hexdigest(), C15_INVENTORY_SHA256)
        self.assertEqual(hashlib.sha256(src_bytes).hexdigest(), C15_RECORD_SHA256)
        self.assertEqual(hashlib.sha256(hlp_bytes).hexdigest(), HELPER_SHA256)

        inv_doc = json.loads(inv_bytes.decode("utf-8"))
        self.assertIs(type(inv_doc["schema_version"]), int)
        self.assertEqual(inv_doc["schema_version"], 1)
        self.assertEqual(inv_doc["stage"], "P1-00b")

        records = inv_doc["records"]
        self.assertEqual(len(records), 1)

        record = records[0]
        self.assertEqual(record["path"], C15_RECORD_PATH)
        self.assertEqual(record["sha256"], C15_RECORD_SHA256)
        self.assertEqual(record["last_commit"], C15_LAST_COMMIT)
        self.assertEqual(record["seed_ids"], C15_SEED_IDS)
        self.assertEqual(record["test_selectors"], [C15_GENUINE_SELECTOR])

        assert_record_evidence(self, record, REPO_ROOT)
        return inv_bytes, src_bytes, hlp_bytes, record

    def test_null_test_selectors_rejected(self) -> None:
        """Verify rejection when test_selectors is null (None)."""
        inv_bytes_before, src_bytes_before, hlp_bytes_before, genuine_record = (
            self._read_and_assert_genuine_oracle()
        )

        mutant_doc = {
            "path": genuine_record["path"],
            "sha256": genuine_record["sha256"],
            "last_commit": genuine_record["last_commit"],
            "seed_ids": list(genuine_record["seed_ids"]),
            "test_selectors": None,
        }
        mutant_json_text = json.dumps(mutant_doc, indent=2)
        self.assertIn('"test_selectors": null', mutant_json_text)
        decoded_mutant = json.loads(mutant_json_text)

        self.assertIsNot(decoded_mutant, genuine_record)
        self.assertIsNot(decoded_mutant["seed_ids"], genuine_record["seed_ids"])
        self.assertEqual(decoded_mutant["seed_ids"], genuine_record["seed_ids"])
        self.assertEqual(set(decoded_mutant.keys()), set(genuine_record.keys()))
        changed_keys = {
            k for k in decoded_mutant if decoded_mutant[k] != genuine_record[k]
        }
        self.assertEqual(changed_keys, {"test_selectors"})

        self.assertEqual(decoded_mutant["path"], C15_RECORD_PATH)
        self.assertEqual(decoded_mutant["sha256"], C15_RECORD_SHA256)
        self.assertEqual(decoded_mutant["last_commit"], C15_LAST_COMMIT)
        self.assertEqual(decoded_mutant["seed_ids"], C15_SEED_IDS)

        self.assertIsNone(decoded_mutant["test_selectors"])
        self.assertIs(type(decoded_mutant["test_selectors"]), type(None))

        expected_regex = r"None is not an instance of <class 'list'>"
        with self.assertRaisesRegex(AssertionError, expected_regex):
            assert_record_evidence(self, decoded_mutant, REPO_ROOT)

        inv_bytes_after = (REPO_ROOT / C15_INVENTORY_REL_PATH).read_bytes()
        src_bytes_after = (REPO_ROOT / C15_RECORD_PATH).read_bytes()
        hlp_bytes_after = (REPO_ROOT / HELPER_REL_PATH).read_bytes()

        self.assertEqual(inv_bytes_after, inv_bytes_before)
        self.assertEqual(src_bytes_after, src_bytes_before)
        self.assertEqual(hlp_bytes_after, hlp_bytes_before)

        self._read_and_assert_genuine_oracle()

    def test_string_test_selectors_rejected(self) -> None:
        """Verify rejection when test_selectors is a string instead of a list."""
        inv_bytes_before, src_bytes_before, hlp_bytes_before, genuine_record = (
            self._read_and_assert_genuine_oracle()
        )

        mutant_doc = {
            "path": genuine_record["path"],
            "sha256": genuine_record["sha256"],
            "last_commit": genuine_record["last_commit"],
            "seed_ids": list(genuine_record["seed_ids"]),
            "test_selectors": C15_GENUINE_SELECTOR,
        }
        mutant_json_text = json.dumps(mutant_doc, indent=2)
        decoded_mutant = json.loads(mutant_json_text)

        self.assertIsNot(decoded_mutant, genuine_record)
        self.assertIsNot(decoded_mutant["seed_ids"], genuine_record["seed_ids"])
        self.assertEqual(decoded_mutant["seed_ids"], genuine_record["seed_ids"])
        self.assertEqual(set(decoded_mutant.keys()), set(genuine_record.keys()))
        changed_keys = {
            k for k in decoded_mutant if decoded_mutant[k] != genuine_record[k]
        }
        self.assertEqual(changed_keys, {"test_selectors"})

        self.assertEqual(decoded_mutant["path"], C15_RECORD_PATH)
        self.assertEqual(decoded_mutant["sha256"], C15_RECORD_SHA256)
        self.assertEqual(decoded_mutant["last_commit"], C15_LAST_COMMIT)
        self.assertEqual(decoded_mutant["seed_ids"], C15_SEED_IDS)

        self.assertIsInstance(decoded_mutant["test_selectors"], str)
        self.assertIs(type(decoded_mutant["test_selectors"]), str)
        self.assertEqual(decoded_mutant["test_selectors"], C15_GENUINE_SELECTOR)

        expected_regex = rf"{re.escape(C15_GENUINE_SELECTOR)}.*is not an instance of <class 'list'>"
        with self.assertRaisesRegex(AssertionError, expected_regex):
            assert_record_evidence(self, decoded_mutant, REPO_ROOT)

        inv_bytes_after = (REPO_ROOT / C15_INVENTORY_REL_PATH).read_bytes()
        src_bytes_after = (REPO_ROOT / C15_RECORD_PATH).read_bytes()
        hlp_bytes_after = (REPO_ROOT / HELPER_REL_PATH).read_bytes()

        self.assertEqual(inv_bytes_after, inv_bytes_before)
        self.assertEqual(src_bytes_after, src_bytes_before)
        self.assertEqual(hlp_bytes_after, hlp_bytes_before)

        self._read_and_assert_genuine_oracle()


if __name__ == "__main__":
    unittest.main()
