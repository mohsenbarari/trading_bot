from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest


EXPECTED_SNAPSHOT_SHA256 = (
    "3a2662a8e60068566582744461bb12cfc3788d3ea04857ca23f99028548f25b7"
)

EXPECTED_SNAPSHOT_DATA = {
    "schema_version": 1,
    "stage": "P1-00b",
    "assignment_id": "WA-P1-00B-FREEZE-20260904-001",
    "inventory": {
        "expected_seed_count": 465,
        "paths": [
            "docs/refactor/two-server/section-1/inventory/surface-api.json",
            "docs/refactor/two-server/section-1/inventory/surface-bot.json",
            "docs/refactor/two-server/section-1/inventory/surface-web.json",
            "docs/refactor/two-server/section-1/inventory/surface-jobs.json",
        ],
    },
    "evidence_sources": {
        "baseline_path": "docs/refactor/two-server/section-1/inventory/scenario-records.json",
        "base_path": "docs/refactor/two-server/section-1/inventory/recent-characterization.json",
        "continuation_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation.json",
        "continuation_2_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-2.json",
        "continuation_3_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-3.json",
        "continuation_4_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-4.json",
        "continuation_5_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-5.json",
        "continuation_6_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-6.json",
        "continuation_7_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-7.json",
        "continuation_8_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-8.json",
        "continuation_9_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-9.json",
        "continuation_10_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-10.json",
        "continuation_11_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-11.json",
    },
    "counts": {
        "baseline_evidenced_seed_count": 135,
        "reconciled_new_seed_count": 252,
        "evidenced_seed_count": 387,
        "remaining_seed_count": 78,
    },
    "coverage_status": "PARTIAL",
    "canonical_blockers": [
        "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
        "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
    ],
}


class SeedCoverage387SnapshotCustodyTests(unittest.TestCase):
    def test_complete_fixed_historical_oracle(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        snapshot_path = (
            repo_root
            / "docs"
            / "refactor"
            / "two-server"
            / "section-1"
            / "inventory"
            / "recent-characterization-seed-coverage-387-snapshot.json"
        )

        self.assertTrue(
            snapshot_path.is_file(),
            f"Snapshot file not found: {snapshot_path}",
        )

        raw_bytes = snapshot_path.read_bytes()
        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        self.assertEqual(
            actual_sha256,
            EXPECTED_SNAPSHOT_SHA256,
            "Snapshot SHA256 mismatch against fixed oracle",
        )

        actual_data = json.loads(raw_bytes.decode("utf-8"))
        self.assertEqual(
            actual_data,
            EXPECTED_SNAPSHOT_DATA,
            "Snapshot JSON data does not match fixed expected manifest",
        )
        self.assertEqual(
            list(actual_data.keys()),
            list(EXPECTED_SNAPSHOT_DATA.keys()),
            "Top-level keys mismatch in historical snapshot",
        )


if __name__ == "__main__":
    unittest.main()
