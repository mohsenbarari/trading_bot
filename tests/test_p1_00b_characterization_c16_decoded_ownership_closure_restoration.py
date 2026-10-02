"""Hermetic C16 decoded-record rejection plus exact ten-file restoration witness.

Stage: P1-00b (IN_PROGRESS)
Assignment: WA-P1-00B-GATE-FIRST-RECONCILIATION-20261002-169
Canonical gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Scenario: P1-00B-C16.DECODED-OWNERSHIP-TEN-FILE-RESTORATION-V1
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_p1_00b_characterization_c16_c17_record_ownership import (
    FROZEN_C16_INVENTORY,
    FROZEN_C17_INVENTORY,
)
from tests.test_p1_00b_characterization_c16_evidence_closure_snapshot import (
    C16_EVIDENCE_CLOSURE_MANIFEST,
    assert_c16_evidence_closure_unchanged,
    freeze_c16_evidence_closure,
)
from tests.test_p1_00b_characterization_recent_inventory_continuation_16_manager_peer import (
    EXPECTED_PREDECESSOR_LAST_COMMIT as C16_EXP_PRED_COMMIT,
    EXPECTED_PREDECESSOR_PATH as C16_EXP_PRED_PATH,
    EXPECTED_PREDECESSOR_SHA as C16_EXP_PRED_SHA,
    EXPECTED_RECORD_LAST_COMMIT as C16_EXP_REC_COMMIT,
    EXPECTED_RECORD_PATH as C16_EXP_REC_PATH,
    EXPECTED_RECORD_SHA as C16_EXP_REC_SHA,
    EXPECTED_SCHEMA_VERSION as C16_EXP_SCHEMA_VERSION,
    EXPECTED_SEED_IDS as C16_EXP_SEED_IDS,
    EXPECTED_STAGE as C16_EXP_STAGE,
    EXPECTED_TEST_SELECTORS as C16_EXP_TEST_SELECTORS,
    validate_continuation_sixteen_provenance,
)
from tests.test_p1_00b_characterization_recent_inventory_continuation_17_missing_target import (
    EXPECTED_PREDECESSOR_LAST_COMMIT as C17_EXP_PRED_COMMIT,
    EXPECTED_PREDECESSOR_PATH as C17_EXP_PRED_PATH,
    EXPECTED_PREDECESSOR_SHA as C17_EXP_PRED_SHA,
    EXPECTED_RECORD_LAST_COMMIT as C17_EXP_REC_COMMIT,
    EXPECTED_RECORD_PATH as C17_EXP_REC_PATH,
    EXPECTED_RECORD_SHA as C17_EXP_REC_SHA,
    EXPECTED_SCHEMA_VERSION as C17_EXP_SCHEMA_VERSION,
    EXPECTED_SEED_IDS as C17_EXP_SEED_IDS,
    EXPECTED_STAGE as C17_EXP_STAGE,
    EXPECTED_TEST_SELECTORS as C17_EXP_TEST_SELECTORS,
    validate_continuation_seventeen_provenance,
)

C16_INVENTORY_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json"
)
C15_INVENTORY_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json"
)
C17_INVENTORY_REL_PATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json"
)
C16_SOURCE_REL_PATH = (
    "tests/test_p1_00b_characterization_users_read_manager_peer_http.py"
)
C17_SOURCE_REL_PATH = (
    "tests/test_p1_00b_characterization_users_read_missing_http.py"
)


class C16DecodedOwnershipClosureRestorationTests(unittest.TestCase):
    """Integrated C16 decoded-record rejection plus exact ten-file restoration suite."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent
        cls.frozen_snapshot = freeze_c16_evidence_closure(cls.repo_root)

    def setUp(self) -> None:
        assert_c16_evidence_closure_unchanged(self.repo_root, self.frozen_snapshot)

    def tearDown(self) -> None:
        assert_c16_evidence_closure_unchanged(self.repo_root, self.frozen_snapshot)

    def _create_temp_closure_tree(self, temp_dir: Path) -> None:
        for rel_path, raw_bytes in self.frozen_snapshot.items():
            target_path = temp_dir / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(raw_bytes)

    def _verify_unaffected_temp_files(self, temp_dir: Path) -> None:
        for rel_path, raw_bytes in self.frozen_snapshot.items():
            if rel_path != C16_INVENTORY_REL_PATH:
                actual_bytes = (temp_dir / rel_path).read_bytes()
                self.assertEqual(
                    actual_bytes,
                    raw_bytes,
                    f"Unaffected file drifted in temporary copy: {rel_path}",
                )

    def _verify_all_paths_against_frozen(
        self, target_root: Path, label: str
    ) -> None:
        for rel_path, expected_bytes in self.frozen_snapshot.items():
            actual_bytes = (target_root / rel_path).read_bytes()
            self.assertEqual(
                actual_bytes,
                expected_bytes,
                f"Byte drift detected in {label} for {rel_path}",
            )

    def _run_baseline_validation(
        self,
    ) -> tuple[dict, dict, bytes, bytes, bytes, bytes]:
        c16_raw = self.frozen_snapshot[C16_INVENTORY_REL_PATH]
        c17_raw = self.frozen_snapshot[C17_INVENTORY_REL_PATH]
        c15_bytes = self.frozen_snapshot[C15_INVENTORY_REL_PATH]
        c16_src_bytes = self.frozen_snapshot[C16_SOURCE_REL_PATH]
        c17_src_bytes = self.frozen_snapshot[C17_SOURCE_REL_PATH]

        decoded_c16 = json.loads(c16_raw.decode("utf-8"))
        decoded_c17 = json.loads(c17_raw.decode("utf-8"))

        # Exact fixture contracts
        self.assertEqual(decoded_c16, FROZEN_C16_INVENTORY)
        self.assertEqual(decoded_c17, FROZEN_C17_INVENTORY)

        self.assertIs(type(decoded_c16["schema_version"]), int)
        self.assertEqual(decoded_c16["schema_version"], C16_EXP_SCHEMA_VERSION)
        self.assertEqual(decoded_c16["stage"], C16_EXP_STAGE)
        self.assertEqual(
            decoded_c16["predecessor_inventory"]["path"], C16_EXP_PRED_PATH
        )
        self.assertEqual(
            decoded_c16["predecessor_inventory"]["sha256"], C16_EXP_PRED_SHA
        )
        self.assertEqual(
            decoded_c16["predecessor_inventory"]["last_commit"],
            C16_EXP_PRED_COMMIT,
        )
        c16_rec = decoded_c16["records"][0]
        self.assertEqual(c16_rec["path"], C16_EXP_REC_PATH)
        self.assertEqual(c16_rec["sha256"], C16_EXP_REC_SHA)
        self.assertEqual(c16_rec["last_commit"], C16_EXP_REC_COMMIT)
        self.assertEqual(c16_rec["seed_ids"], C16_EXP_SEED_IDS)
        self.assertEqual(c16_rec["test_selectors"], C16_EXP_TEST_SELECTORS)

        self.assertIs(type(decoded_c17["schema_version"]), int)
        self.assertEqual(decoded_c17["schema_version"], C17_EXP_SCHEMA_VERSION)
        self.assertEqual(decoded_c17["stage"], C17_EXP_STAGE)
        self.assertEqual(
            decoded_c17["predecessor_inventory"]["path"], C17_EXP_PRED_PATH
        )
        self.assertEqual(
            decoded_c17["predecessor_inventory"]["sha256"], C17_EXP_PRED_SHA
        )
        self.assertEqual(
            decoded_c17["predecessor_inventory"]["last_commit"],
            C17_EXP_PRED_COMMIT,
        )
        c17_rec = decoded_c17["records"][0]
        self.assertEqual(c17_rec["path"], C17_EXP_REC_PATH)
        self.assertEqual(c17_rec["sha256"], C17_EXP_REC_SHA)
        self.assertEqual(c17_rec["last_commit"], C17_EXP_REC_COMMIT)
        self.assertEqual(c17_rec["seed_ids"], C17_EXP_SEED_IDS)
        self.assertEqual(c17_rec["test_selectors"], C17_EXP_TEST_SELECTORS)

        # Shared seed alone is not ownership
        self.assertEqual(c16_rec["seed_ids"], c17_rec["seed_ids"])

        # Explicitly pin behavior-source SHA and path distinction
        c16_inv_sha = hashlib.sha256(c16_raw).hexdigest()
        c16_src_sha = hashlib.sha256(c16_src_bytes).hexdigest()
        self.assertNotEqual(C16_INVENTORY_REL_PATH, C16_SOURCE_REL_PATH)
        self.assertNotEqual(c16_inv_sha, c16_src_sha)
        self.assertEqual(
            c16_inv_sha,
            "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a",
        )
        self.assertEqual(
            c16_src_sha,
            "f093210c5dadda895a370ec1427607c3eade7e593e3133d3551881f2e8e20bcd",
        )
        self.assertEqual(c16_rec["sha256"], c16_src_sha)
        self.assertNotEqual(c16_rec["sha256"], c16_inv_sha)

        c17_inv_sha = hashlib.sha256(c17_raw).hexdigest()
        c17_src_sha = hashlib.sha256(c17_src_bytes).hexdigest()
        self.assertNotEqual(C17_INVENTORY_REL_PATH, C17_SOURCE_REL_PATH)
        self.assertNotEqual(c17_inv_sha, c17_src_sha)
        self.assertEqual(
            c17_inv_sha,
            "f3d6f12ade5bb8933472a727ba3e880070bdca758148b2a98ce81e404c18bd15",
        )
        self.assertEqual(
            c17_src_sha,
            "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb",
        )
        self.assertEqual(c17_rec["sha256"], c17_src_sha)
        self.assertNotEqual(c17_rec["sha256"], c17_inv_sha)

        # Baseline genuine validation
        validate_continuation_sixteen_provenance(
            inventory=decoded_c16,
            predecessor_bytes=c15_bytes,
            source_bytes=c16_src_bytes,
        )
        validate_continuation_seventeen_provenance(
            inventory=decoded_c17,
            predecessor_bytes=c16_raw,
            source_bytes=c17_src_bytes,
        )

        return (
            decoded_c16,
            decoded_c17,
            c15_bytes,
            c16_raw,
            c16_src_bytes,
            c17_src_bytes,
        )

    def test_c17_substitution_rejected_with_complete_closure_restoration(
        self,
    ) -> None:
        (
            decoded_c16,
            decoded_c17,
            c15_bytes,
            c16_raw,
            c16_src_bytes,
            c17_src_bytes,
        ) = self._run_baseline_validation()

        temp_dir_obj = tempfile.TemporaryDirectory()
        try:
            temp_dir = Path(temp_dir_obj.name)
            self._create_temp_closure_tree(temp_dir)

            # Before mutation checks
            assert_c16_evidence_closure_unchanged(
                self.repo_root, self.frozen_snapshot
            )
            assert_c16_evidence_closure_unchanged(temp_dir, self.frozen_snapshot)
            self._verify_all_paths_against_frozen(
                self.repo_root, "canonical before mutation"
            )
            self._verify_all_paths_against_frozen(
                temp_dir, "temporary before mutation"
            )

            # Form mutated C16 inventory with substituted C17 record
            mutated_c16 = copy.deepcopy(decoded_c16)
            mutated_c16["records"] = [copy.deepcopy(decoded_c17["records"][0])]

            temp_c16_path = temp_dir / C16_INVENTORY_REL_PATH
            serialized_bytes = (
                json.dumps(mutated_c16, indent=2).encode("utf-8") + b"\n"
            )
            temp_c16_path.write_bytes(serialized_bytes)

            # JSON decode actual temporary bytes and assert contracts
            actual_temp_bytes = temp_c16_path.read_bytes()
            decoded_actual = json.loads(actual_temp_bytes.decode("utf-8"))

            self.assertEqual(
                decoded_actual["schema_version"], decoded_c16["schema_version"]
            )
            self.assertEqual(decoded_actual["stage"], decoded_c16["stage"])
            self.assertEqual(
                decoded_actual["predecessor_inventory"],
                decoded_c16["predecessor_inventory"],
            )
            self.assertEqual(
                decoded_actual["records"], [decoded_c17["records"][0]]
            )
            self.assertNotEqual(
                decoded_actual["records"], decoded_c16["records"]
            )

            # Prove validator rejection with specific ValueError
            with self.assertRaises(ValueError) as val_ctx:
                validate_continuation_sixteen_provenance(
                    inventory=decoded_actual,
                    predecessor_bytes=c15_bytes,
                    source_bytes=c16_src_bytes,
                )
            self.assertIn("Invalid record path", str(val_ctx.exception))

            # Prove whole-ten-file byte oracle detects drift
            with self.assertRaises(AssertionError) as oracle_ctx:
                assert_c16_evidence_closure_unchanged(
                    temp_dir, self.frozen_snapshot
                )
            self.assertIn(
                "Receipt byte drift detected", str(oracle_ctx.exception)
            )
            self.assertIn("continuation-16.json", str(oracle_ctx.exception))

            # Immediately after rejection checks
            assert_c16_evidence_closure_unchanged(
                self.repo_root, self.frozen_snapshot
            )
            self._verify_all_paths_against_frozen(
                self.repo_root, "canonical immediately after rejection"
            )
            self._verify_unaffected_temp_files(temp_dir)

            # Restoration
            temp_c16_path.write_bytes(c16_raw)

            # After restoration checks
            assert_c16_evidence_closure_unchanged(
                self.repo_root, self.frozen_snapshot
            )
            assert_c16_evidence_closure_unchanged(temp_dir, self.frozen_snapshot)
            self._verify_all_paths_against_frozen(
                self.repo_root, "canonical after restoration"
            )
            self._verify_all_paths_against_frozen(
                temp_dir, "temporary after restoration"
            )

            # Re-run BOTH genuine provenance validators
            restored_c16_inv = json.loads(
                temp_c16_path.read_bytes().decode("utf-8")
            )
            restored_c17_inv = json.loads(
                (temp_dir / C17_INVENTORY_REL_PATH).read_bytes().decode("utf-8")
            )
            validate_continuation_sixteen_provenance(
                inventory=restored_c16_inv,
                predecessor_bytes=c15_bytes,
                source_bytes=c16_src_bytes,
            )
            validate_continuation_seventeen_provenance(
                inventory=restored_c17_inv,
                predecessor_bytes=temp_c16_path.read_bytes(),
                source_bytes=c17_src_bytes,
            )
        finally:
            temp_dir_obj.cleanup()

        # Recheck canonical closure after temporary directory cleanup
        assert_c16_evidence_closure_unchanged(
            self.repo_root, self.frozen_snapshot
        )
        self._verify_all_paths_against_frozen(
            self.repo_root, "canonical after temp cleanup"
        )

    def test_c17_append_rejected_with_complete_closure_restoration(
        self,
    ) -> None:
        (
            decoded_c16,
            decoded_c17,
            c15_bytes,
            c16_raw,
            c16_src_bytes,
            c17_src_bytes,
        ) = self._run_baseline_validation()

        temp_dir_obj = tempfile.TemporaryDirectory()
        try:
            temp_dir = Path(temp_dir_obj.name)
            self._create_temp_closure_tree(temp_dir)

            # Before mutation checks
            assert_c16_evidence_closure_unchanged(
                self.repo_root, self.frozen_snapshot
            )
            assert_c16_evidence_closure_unchanged(temp_dir, self.frozen_snapshot)
            self._verify_all_paths_against_frozen(
                self.repo_root, "canonical before mutation"
            )
            self._verify_all_paths_against_frozen(
                temp_dir, "temporary before mutation"
            )

            # Form mutated C16 inventory with appended C17 record [C16, C17]
            mutated_c16 = copy.deepcopy(decoded_c16)
            mutated_c16["records"] = [
                copy.deepcopy(decoded_c16["records"][0]),
                copy.deepcopy(decoded_c17["records"][0]),
            ]

            temp_c16_path = temp_dir / C16_INVENTORY_REL_PATH
            serialized_bytes = (
                json.dumps(mutated_c16, indent=2).encode("utf-8") + b"\n"
            )
            temp_c16_path.write_bytes(serialized_bytes)

            # JSON decode actual temporary bytes and assert contracts
            actual_temp_bytes = temp_c16_path.read_bytes()
            decoded_actual = json.loads(actual_temp_bytes.decode("utf-8"))

            self.assertEqual(
                decoded_actual["schema_version"], decoded_c16["schema_version"]
            )
            self.assertEqual(decoded_actual["stage"], decoded_c16["stage"])
            self.assertEqual(
                decoded_actual["predecessor_inventory"],
                decoded_c16["predecessor_inventory"],
            )
            self.assertEqual(
                decoded_actual["records"],
                [decoded_c16["records"][0], decoded_c17["records"][0]],
            )
            self.assertEqual(len(decoded_actual["records"]), 2)

            # Prove validator rejection with specific ValueError
            with self.assertRaises(ValueError) as val_ctx:
                validate_continuation_sixteen_provenance(
                    inventory=decoded_actual,
                    predecessor_bytes=c15_bytes,
                    source_bytes=c16_src_bytes,
                )
            self.assertIn("Expected exactly one record", str(val_ctx.exception))

            # Prove whole-ten-file byte oracle detects drift
            with self.assertRaises(AssertionError) as oracle_ctx:
                assert_c16_evidence_closure_unchanged(
                    temp_dir, self.frozen_snapshot
                )
            self.assertIn(
                "Receipt byte drift detected", str(oracle_ctx.exception)
            )
            self.assertIn("continuation-16.json", str(oracle_ctx.exception))

            # Immediately after rejection checks
            assert_c16_evidence_closure_unchanged(
                self.repo_root, self.frozen_snapshot
            )
            self._verify_all_paths_against_frozen(
                self.repo_root, "canonical immediately after rejection"
            )
            self._verify_unaffected_temp_files(temp_dir)

            # Restoration
            temp_c16_path.write_bytes(c16_raw)

            # After restoration checks
            assert_c16_evidence_closure_unchanged(
                self.repo_root, self.frozen_snapshot
            )
            assert_c16_evidence_closure_unchanged(temp_dir, self.frozen_snapshot)
            self._verify_all_paths_against_frozen(
                self.repo_root, "canonical after restoration"
            )
            self._verify_all_paths_against_frozen(
                temp_dir, "temporary after restoration"
            )

            # Re-run BOTH genuine provenance validators
            restored_c16_inv = json.loads(
                temp_c16_path.read_bytes().decode("utf-8")
            )
            restored_c17_inv = json.loads(
                (temp_dir / C17_INVENTORY_REL_PATH).read_bytes().decode("utf-8")
            )
            validate_continuation_sixteen_provenance(
                inventory=restored_c16_inv,
                predecessor_bytes=c15_bytes,
                source_bytes=c16_src_bytes,
            )
            validate_continuation_seventeen_provenance(
                inventory=restored_c17_inv,
                predecessor_bytes=temp_c16_path.read_bytes(),
                source_bytes=c17_src_bytes,
            )
        finally:
            temp_dir_obj.cleanup()

        # Recheck canonical closure after temporary directory cleanup
        assert_c16_evidence_closure_unchanged(
            self.repo_root, self.frozen_snapshot
        )
        self._verify_all_paths_against_frozen(
            self.repo_root, "canonical after temp cleanup"
        )


if __name__ == "__main__":
    unittest.main()
