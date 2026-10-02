"""Characterization tests for inventory sha256 required-key presence rejection in assert_record_evidence.

Stage: P1-00b (IN_PROGRESS)
Assignment: WA-P1-00B-AFTER-OBSERVER-PROJECTION-20261002-161 (SHA256 b2dbac223995215c207ba881b3eea2673bb6b428a7962cb9beb9562c69156883)
Parent: WA-P1-00B-FREEZE-20260904-001
Canonical Open Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Tracked reference: docs/refactor/two-server/section-1/00-stage-ledger.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Shared seed: API-IDENTITY-GET-USERS-READ-ALL-USERS-L407

Scenario:
- Scenario 1 (test_missing_sha256_key_rejected): Standalone C15 record omitting
  the required sha256 key is rejected by assert_record_evidence with an
  AssertionError indicating 'sha256' not found in record dictionary.
  Sole scenario: P1-00B.inventory.record.sha256_missing_key.

Exclusions:
- Excludes accepted null and string test_selectors scenarios in test_p1_00b_characterization_inventory_selector_list_domain.py.
- Excludes missing test_selectors key scenario in test_p1_00b_characterization_inventory_selector_presence.py.
- Excludes selector-domain, resolution, path, or hash mismatch scenarios.
- Excludes predecessor and schema domain mutations.
- Excludes application, HTTP runtime, and authorization-policy execution.
- Excludes read_user self-success and super-admin rejection.
- Excludes persistence, retry, reload, and origin matrix cells.
- Excludes ledger, inventory, count, and checkpoint edits.
- Excludes sixth behavior acceptance and any seed/blocker/Stage closure.

Prerequisite status:
- Distinct test-only helper-consumer prerequisite for P1-00b. Adds no accepted behavior/seed/cell
  and is not a seed-handler batch.
- Current accepted ledger and reconciliation-17 remain authoritative: reconciliation=3/5,
  pending-five=5, units_since_reconciliation=5; remaining C15 empty-list HTTP200 and C16
  manager-peer HTTP403 remain pending. Coverage remains 387/465 with 78 remaining; both High blockers
  open (P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE, P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN); 24 origin
  cells unresolved; P1-00 COMPLETE, P1-00b IN_PROGRESS.
- Defer normal161-c5-c16-incremental-reconciliation-20261002 and preserve earlier deferrals:
  rejected C15 machine checkpoint, continuation-12, 153/154/155/157-C001, normal159 C1/C2,
  rejected policy/seed proposals.
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


class InventoryHashPresenceTests(unittest.TestCase):
    """Characterization tests for inventory sha256 key presence rejection."""

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
        expected_record = {
            "path": C15_RECORD_PATH,
            "sha256": C15_RECORD_SHA256,
            "last_commit": C15_LAST_COMMIT,
            "seed_ids": list(C15_SEED_IDS),
            "test_selectors": [C15_GENUINE_SELECTOR],
        }
        self.assertEqual(record["path"], C15_RECORD_PATH)
        self.assertEqual(record["sha256"], C15_RECORD_SHA256)
        self.assertEqual(record["last_commit"], C15_LAST_COMMIT)
        self.assertEqual(record["seed_ids"], C15_SEED_IDS)
        self.assertEqual(record["test_selectors"], [C15_GENUINE_SELECTOR])
        self.assertEqual(record, expected_record)

        assert_record_evidence(self, record, REPO_ROOT)
        return inv_bytes, src_bytes, hlp_bytes, record

    def test_missing_sha256_key_rejected(self) -> None:
        """Verify rejection when sha256 key is absent from standalone C15 record."""
        inv_bytes_before, src_bytes_before, hlp_bytes_before, genuine_record = (
            self._read_and_assert_genuine_oracle()
        )

        malformed_doc = {
            "path": C15_RECORD_PATH,
            "last_commit": C15_LAST_COMMIT,
            "seed_ids": list(C15_SEED_IDS),
            "test_selectors": [C15_GENUINE_SELECTOR],
        }
        malformed_json_text = json.dumps(malformed_doc, indent=2)
        self.assertNotIn("sha256", malformed_json_text)
        decoded_malformed = json.loads(malformed_json_text)

        self.assertIsNot(decoded_malformed, genuine_record)
        self.assertIsNot(decoded_malformed["seed_ids"], genuine_record["seed_ids"])
        self.assertIsNot(
            decoded_malformed["test_selectors"], genuine_record["test_selectors"]
        )
        self.assertEqual(decoded_malformed["seed_ids"], genuine_record["seed_ids"])
        self.assertEqual(
            decoded_malformed["test_selectors"], genuine_record["test_selectors"]
        )

        self.assertEqual(
            set(genuine_record.keys()) - set(decoded_malformed.keys()),
            {"sha256"},
        )
        self.assertEqual(
            set(decoded_malformed.keys()) - set(genuine_record.keys()),
            set(),
        )
        self.assertEqual(
            set(genuine_record.keys()) ^ set(decoded_malformed.keys()),
            {"sha256"},
        )

        self.assertEqual(decoded_malformed["path"], C15_RECORD_PATH)
        self.assertEqual(decoded_malformed["last_commit"], C15_LAST_COMMIT)
        self.assertEqual(decoded_malformed["seed_ids"], C15_SEED_IDS)
        self.assertEqual(decoded_malformed["test_selectors"], [C15_GENUINE_SELECTOR])
        self.assertNotIn("sha256", decoded_malformed)

        # Before narrow rejection scope: validate the SAME genuine frozen oracle
        assert_record_evidence(self, genuine_record, REPO_ROOT)

        malformed_before_repr = json.dumps(decoded_malformed, sort_keys=True)
        expected_regex = re.escape("'sha256' not found in")
        with self.assertRaisesRegex(AssertionError, expected_regex):
            assert_record_evidence(self, decoded_malformed, REPO_ROOT)

        malformed_after_repr = json.dumps(decoded_malformed, sort_keys=True)
        self.assertEqual(malformed_after_repr, malformed_before_repr)
        self.assertNotIn("sha256", decoded_malformed)
        self.assertEqual(decoded_malformed["path"], C15_RECORD_PATH)
        self.assertEqual(decoded_malformed["last_commit"], C15_LAST_COMMIT)
        self.assertEqual(decoded_malformed["seed_ids"], C15_SEED_IDS)
        self.assertEqual(decoded_malformed["test_selectors"], [C15_GENUINE_SELECTOR])

        inv_bytes_after = (REPO_ROOT / C15_INVENTORY_REL_PATH).read_bytes()
        src_bytes_after = (REPO_ROOT / C15_RECORD_PATH).read_bytes()
        hlp_bytes_after = (REPO_ROOT / HELPER_REL_PATH).read_bytes()

        self.assertEqual(inv_bytes_after, inv_bytes_before)
        self.assertEqual(src_bytes_after, src_bytes_before)
        self.assertEqual(hlp_bytes_after, hlp_bytes_before)

        self.assertEqual(
            hashlib.sha256(inv_bytes_after).hexdigest(), C15_INVENTORY_SHA256
        )
        self.assertEqual(
            hashlib.sha256(src_bytes_after).hexdigest(), C15_RECORD_SHA256
        )
        self.assertEqual(
            hashlib.sha256(hlp_bytes_after).hexdigest(), HELPER_SHA256
        )

        reloaded_inv_doc = json.loads(inv_bytes_after.decode("utf-8"))
        self.assertIs(type(reloaded_inv_doc["schema_version"]), int)
        self.assertEqual(reloaded_inv_doc["schema_version"], 1)
        self.assertEqual(reloaded_inv_doc["stage"], "P1-00b")
        self.assertEqual(len(reloaded_inv_doc["records"]), 1)

        reloaded_record = reloaded_inv_doc["records"][0]
        self.assertEqual(reloaded_record["path"], C15_RECORD_PATH)
        self.assertEqual(reloaded_record["sha256"], C15_RECORD_SHA256)
        self.assertEqual(reloaded_record["last_commit"], C15_LAST_COMMIT)
        self.assertEqual(reloaded_record["seed_ids"], C15_SEED_IDS)
        self.assertEqual(reloaded_record["test_selectors"], [C15_GENUINE_SELECTOR])

        assert_record_evidence(self, reloaded_record, REPO_ROOT)

        self.assertEqual(genuine_record["path"], C15_RECORD_PATH)
        self.assertEqual(genuine_record["sha256"], C15_RECORD_SHA256)
        self.assertEqual(genuine_record["last_commit"], C15_LAST_COMMIT)
        self.assertEqual(genuine_record["seed_ids"], C15_SEED_IDS)
        self.assertEqual(genuine_record["test_selectors"], [C15_GENUINE_SELECTOR])
        self.assertEqual(genuine_record, reloaded_record)
        assert_record_evidence(self, genuine_record, REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
