from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_p1_00b_characterization_c13_canonical_seed_membership import (
    compute_canonical_accounting,
    freeze_evidence_manifest,
)
from tests.test_p1_00b_characterization_c16_evidence_closure_snapshot import (
    C16_EVIDENCE_CLOSURE_MANIFEST,
    assert_c16_evidence_closure_unchanged,
    freeze_c16_evidence_closure,
)
from tests.test_p1_00b_characterization_receipt_byte_preservation import (
    assert_receipt_bytes_unchanged,
)

ACCEPTED_HEAD_COMMIT = "7d9e017f0ae4c543b652066667ae6746dcb594f2"
C16_DECODED_RESTORATION_COMMIT = "c4ae8b55ffa2531842e11a65ece4e6eb2a7b9c32"
C15_CHECKPOINT_COMMIT = "c66dabdfe845d6f69623986c0705c415dfcf089c"

SHARED_SEED_ID = "API-IDENTITY-GET-USERS-READ-USER-L440"

C16_RELPATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json"
C17_RELPATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json"
C15_CHECKPOINT_RELPATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-c15-from-c17.json"

C16_RECORD_PATH = "tests/test_p1_00b_characterization_users_read_manager_peer_http.py"
C16_RECORD_SHA = "f093210c5dadda895a370ec1427607c3eade7e593e3133d3551881f2e8e20bcd"
C16_BEHAVIOR_COMMIT = "05fe0d5c2a5dae727aa7219549843b1ce6235945"
C16_TEST_SELECTOR = (
    "tests/test_p1_00b_characterization_users_read_manager_peer_http.py::"
    "UsersReadManagerPeerHttpCharacterizationTests::"
    "test_middle_manager_peer_read_returns_http_403_before_relation_loading"
)

C17_RECORD_PATH = "tests/test_p1_00b_characterization_users_read_missing_http.py"
C17_RECORD_SHA = "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb"
C17_BEHAVIOR_COMMIT = "0f3deb613251c7c5f39822b2a1f0e5532e5e140f"
C17_TEST_SELECTOR = (
    "tests/test_p1_00b_characterization_users_read_missing_http.py::"
    "UsersReadMissingHttpCharacterizationTests::"
    "test_authorized_missing_target_returns_http_404_after_single_get"
)

PINNED_ADDITIONAL_MANIFEST: dict[str, str] = {
    "tests/test_p1_00b_characterization_c13_canonical_seed_membership.py": (
        "d09f1427b1f2dcb3523aac7cc646ce72b83acafdc2fede564309c9d966b9149a"
    ),
    "tests/test_p1_00b_characterization_c16_evidence_closure_snapshot.py": (
        "cf16f8be4df207b43c45d4ce5e87bc091cd3b325f62c3d2f2c689b212f8f4432"
    ),
    "tests/test_p1_00b_characterization_receipt_byte_preservation.py": (
        "3b3742b12c0c51b716630fda1ec9dd34f8d5758a15cfb6262bdc31a0c441982f"
    ),
}


def assert_c16_c17_shared_seed_contribution(
    reconciliation_data: dict,
    frozen_evidence_bytes: dict[str, bytes],
    c16_continuation_data: dict,
    c17_continuation_data: dict,
) -> None:
    """Validate that C16/C17 shared seed contribution is exactly 1 and reject false double-counts."""
    (
        _counts,
        _surface_seeds,
        _baseline_seeds,
        _new_seeds,
        evidenced_seeds,
        _remaining_seeds,
    ) = compute_canonical_accounting(frozen_evidence_bytes)

    c16_seeds: set[str] = set()
    for rec in c16_continuation_data.get("records", []):
        c16_seeds.update(rec.get("seed_ids", []))

    c17_seeds: set[str] = set()
    for rec in c17_continuation_data.get("records", []):
        c17_seeds.update(rec.get("seed_ids", []))

    shared_seeds = c16_seeds & c17_seeds
    shared_union = c16_seeds | c17_seeds
    contributed_seeds = shared_union - evidenced_seeds
    expected_distinct_count = len(contributed_seeds)

    if expected_distinct_count != 1:
        raise ValueError(
            f"Expected exactly 1 contributed seed from C16/C17 union, got {expected_distinct_count}"
        )

    seed_accounting = reconciliation_data.get("seed_accounting")
    if type(seed_accounting) is not dict:
        raise ValueError("reconciliation_data missing seed_accounting dict")

    prospective_count = seed_accounting.get("prospective_distinct_seed_count")
    if type(prospective_count) is not int:
        raise ValueError(
            f"Invalid prospective_distinct_seed_count type: expected int, got {type(prospective_count)}"
        )

    prospective_seeds = seed_accounting.get("prospective_distinct_seeds")
    if (
        type(prospective_seeds) is not list
        or not all(type(s) is str for s in prospective_seeds)
    ):
        raise ValueError("Invalid prospective_distinct_seeds: expected list of str")

    if prospective_count != expected_distinct_count:
        if prospective_count > expected_distinct_count:
            shared_id = sorted(shared_seeds)[0] if shared_seeds else "UNKNOWN"
            raise ValueError(
                f"Double-count detected for shared seed '{shared_id}': "
                f"expected prospective_distinct_seed_count={expected_distinct_count}, "
                f"got {prospective_count} (false double-count across C16 and C17)"
            )
        raise ValueError(
            f"prospective_distinct_seed_count mismatch: expected {expected_distinct_count}, "
            f"got {prospective_count}"
        )

    if set(prospective_seeds) != contributed_seeds:
        raise ValueError(
            f"prospective_distinct_seeds mismatch: expected {sorted(contributed_seeds)}, "
            f"got {sorted(prospective_seeds)}"
        )


def assert_complete_closure_unchanged(
    target_root: Path,
    expected_snapshot: dict[str, bytes],
) -> None:
    """Assert all files across the complete closure match expected snapshot bytes."""
    for rel_path, expected_bytes in expected_snapshot.items():
        file_path = target_root / rel_path
        if not file_path.is_file():
            raise AssertionError(f"Closure file missing: {file_path}")
        assert_receipt_bytes_unchanged(file_path, expected_bytes)


class C16C17SharedSeedContributionTests(unittest.TestCase):
    def test_false_double_count_rejected_and_complete_evidence_restored(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        frozen_canonical_bytes = freeze_evidence_manifest(repo_root)
        frozen_c16_closure_bytes = freeze_c16_evidence_closure(repo_root)

        frozen_additional_bytes: dict[str, bytes] = {}
        for rel_path, expected_sha in PINNED_ADDITIONAL_MANIFEST.items():
            file_path = repo_root / rel_path
            if not file_path.is_file():
                raise AssertionError(f"Pinned helper file missing: {file_path}")
            raw_bytes = file_path.read_bytes()
            actual_sha = hashlib.sha256(raw_bytes).hexdigest()
            if actual_sha != expected_sha:
                raise AssertionError(
                    f"Pinned helper SHA mismatch for {rel_path}: expected {expected_sha}, got {actual_sha}"
                )
            frozen_additional_bytes[rel_path] = raw_bytes

        complete_closure_snapshot: dict[str, bytes] = {
            **frozen_canonical_bytes,
            **frozen_c16_closure_bytes,
            **frozen_additional_bytes,
        }

        self.assertEqual(ACCEPTED_HEAD_COMMIT, "7d9e017f0ae4c543b652066667ae6746dcb594f2")
        self.assertEqual(C16_DECODED_RESTORATION_COMMIT, "c4ae8b55ffa2531842e11a65ece4e6eb2a7b9c32")
        self.assertEqual(C15_CHECKPOINT_COMMIT, "c66dabdfe845d6f69623986c0705c415dfcf089c")

        (
            counts,
            surface_seeds,
            baseline_seeds,
            new_beyond_baseline,
            evidenced_seeds,
            remaining_seeds,
        ) = compute_canonical_accounting(frozen_canonical_bytes)

        self.assertEqual(counts["docs/refactor/two-server/section-1/inventory/surface-api.json"], 217)
        self.assertEqual(counts["docs/refactor/two-server/section-1/inventory/surface-bot.json"], 203)
        self.assertEqual(counts["docs/refactor/two-server/section-1/inventory/surface-web.json"], 30)
        self.assertEqual(counts["docs/refactor/two-server/section-1/inventory/surface-jobs.json"], 15)
        self.assertEqual(len(surface_seeds), 465)
        self.assertEqual(len(baseline_seeds), 135)
        self.assertEqual(len(new_beyond_baseline), 252)
        self.assertEqual(len(evidenced_seeds), 387)
        self.assertEqual(len(remaining_seeds), 78)

        self.assertIn(SHARED_SEED_ID, surface_seeds)
        self.assertIn(SHARED_SEED_ID, remaining_seeds)
        self.assertNotIn(SHARED_SEED_ID, evidenced_seeds)
        self.assertNotIn(SHARED_SEED_ID, baseline_seeds)
        self.assertNotIn(SHARED_SEED_ID, new_beyond_baseline)

        c16_doc = json.loads(frozen_c16_closure_bytes[C16_RELPATH].decode("utf-8"))
        self.assertEqual(len(c16_doc["records"]), 1)
        c16_rec = c16_doc["records"][0]
        self.assertEqual(c16_rec["path"], C16_RECORD_PATH)
        self.assertEqual(c16_rec["sha256"], C16_RECORD_SHA)
        self.assertEqual(c16_rec["last_commit"], C16_BEHAVIOR_COMMIT)
        self.assertEqual(c16_rec["seed_ids"], [SHARED_SEED_ID])
        self.assertEqual(c16_rec["test_selectors"], [C16_TEST_SELECTOR])

        c17_doc = json.loads(frozen_c16_closure_bytes[C17_RELPATH].decode("utf-8"))
        self.assertEqual(len(c17_doc["records"]), 1)
        c17_rec = c17_doc["records"][0]
        self.assertEqual(c17_rec["path"], C17_RECORD_PATH)
        self.assertEqual(c17_rec["sha256"], C17_RECORD_SHA)
        self.assertEqual(c17_rec["last_commit"], C17_BEHAVIOR_COMMIT)
        self.assertEqual(c17_rec["seed_ids"], [SHARED_SEED_ID])
        self.assertEqual(c17_rec["test_selectors"], [C17_TEST_SELECTOR])

        actual_c16_source_sha = hashlib.sha256(
            frozen_c16_closure_bytes[C16_RECORD_PATH]
        ).hexdigest()
        self.assertEqual(actual_c16_source_sha, C16_RECORD_SHA)

        actual_c17_source_sha = hashlib.sha256(
            frozen_c16_closure_bytes[C17_RECORD_PATH]
        ).hexdigest()
        self.assertEqual(actual_c17_source_sha, C17_RECORD_SHA)

        c16_seeds = set(c16_rec["seed_ids"])
        c17_seeds = set(c17_rec["seed_ids"])
        self.assertEqual(c16_seeds, {SHARED_SEED_ID})
        self.assertEqual(c17_seeds, {SHARED_SEED_ID})

        shared_union = c16_seeds | c17_seeds
        self.assertEqual(shared_union, {SHARED_SEED_ID})
        contribution = shared_union - evidenced_seeds
        self.assertEqual(contribution, {SHARED_SEED_ID})
        self.assertEqual(len(contribution), 1)

        c16_addition_after_c17 = c16_seeds - (evidenced_seeds | c17_seeds)
        self.assertEqual(c16_addition_after_c17, set())
        self.assertEqual(len(c16_addition_after_c17), 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for rel_path, raw_bytes in complete_closure_snapshot.items():
                dest = temp_path / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw_bytes)

            assert_c16_evidence_closure_unchanged(repo_root, frozen_c16_closure_bytes)
            assert_complete_closure_unchanged(repo_root, complete_closure_snapshot)
            assert_c16_evidence_closure_unchanged(temp_path, frozen_c16_closure_bytes)
            assert_complete_closure_unchanged(temp_path, complete_closure_snapshot)

            c15_temp_path = temp_path / C15_CHECKPOINT_RELPATH
            genuine_c15_data = json.loads(c15_temp_path.read_bytes().decode("utf-8"))
            assert_c16_c17_shared_seed_contribution(
                genuine_c15_data, frozen_canonical_bytes, c16_doc, c17_doc
            )

            mutated_c15_data = copy.deepcopy(genuine_c15_data)
            mutated_c15_data["seed_accounting"]["prospective_distinct_seed_count"] = 2
            c15_temp_path.write_bytes(
                json.dumps(mutated_c15_data, indent=2).encode("utf-8") + b"\n"
            )

            for rel_path, raw_bytes in complete_closure_snapshot.items():
                if rel_path != C15_CHECKPOINT_RELPATH:
                    assert_receipt_bytes_unchanged(temp_path / rel_path, raw_bytes)

            with self.assertRaises(AssertionError):
                assert_receipt_bytes_unchanged(
                    c15_temp_path,
                    complete_closure_snapshot[C15_CHECKPOINT_RELPATH],
                )
            with self.assertRaises(AssertionError):
                assert_c16_evidence_closure_unchanged(
                    temp_path,
                    frozen_c16_closure_bytes,
                )
            with self.assertRaises(AssertionError):
                assert_complete_closure_unchanged(
                    temp_path,
                    complete_closure_snapshot,
                )

            decoded_mutated = json.loads(c15_temp_path.read_bytes().decode("utf-8"))
            self.assertEqual(
                decoded_mutated["seed_accounting"]["prospective_distinct_seed_count"],
                2,
            )
            with self.assertRaises(ValueError) as cm:
                assert_c16_c17_shared_seed_contribution(
                    decoded_mutated, frozen_canonical_bytes, c16_doc, c17_doc
                )
            err_msg = str(cm.exception)
            self.assertIn(SHARED_SEED_ID, err_msg)
            self.assertIn("double-count", err_msg.lower())

            assert_c16_evidence_closure_unchanged(repo_root, frozen_c16_closure_bytes)
            assert_complete_closure_unchanged(repo_root, complete_closure_snapshot)

            c15_temp_path.write_bytes(complete_closure_snapshot[C15_CHECKPOINT_RELPATH])
            assert_receipt_bytes_unchanged(
                c15_temp_path,
                complete_closure_snapshot[C15_CHECKPOINT_RELPATH],
            )

            assert_c16_evidence_closure_unchanged(temp_path, frozen_c16_closure_bytes)
            assert_complete_closure_unchanged(temp_path, complete_closure_snapshot)
            assert_c16_evidence_closure_unchanged(repo_root, frozen_c16_closure_bytes)
            assert_complete_closure_unchanged(repo_root, complete_closure_snapshot)

            restored_c15_data = json.loads(c15_temp_path.read_bytes().decode("utf-8"))
            assert_c16_c17_shared_seed_contribution(
                restored_c15_data, frozen_canonical_bytes, c16_doc, c17_doc
            )

        self.assertFalse(temp_path.exists())
        assert_c16_evidence_closure_unchanged(repo_root, frozen_c16_closure_bytes)
        assert_complete_closure_unchanged(repo_root, complete_closure_snapshot)


if __name__ == "__main__":
    unittest.main()
