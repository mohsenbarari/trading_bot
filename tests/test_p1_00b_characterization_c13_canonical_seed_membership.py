from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_p1_00b_characterization_receipt_byte_preservation import (
    assert_receipt_bytes_unchanged,
)
from tests.test_p1_00b_characterization_recent_inventory_continuation_13_self_export import (
    EXPECTED_PREDECESSOR_LAST_COMMIT,
    EXPECTED_PREDECESSOR_PATH,
    EXPECTED_PREDECESSOR_SHA,
    EXPECTED_RECORD_LAST_COMMIT,
    EXPECTED_RECORD_PATH,
    EXPECTED_RECORD_SHA,
    EXPECTED_SCHEMA_VERSION,
    EXPECTED_SEED_IDS,
    EXPECTED_STAGE,
    EXPECTED_TEST_SELECTORS,
    validate_continuation_thirteen_provenance,
)

FROZEN_HEAD_MANIFEST: dict[str, str] = {
    "docs/refactor/two-server/section-1/inventory/recent-characterization-seed-coverage-387-snapshot.json": "3a2662a8e60068566582744461bb12cfc3788d3ea04857ca23f99028548f25b7",
    "docs/refactor/two-server/section-1/inventory/surface-api.json": "d263b03bebbce2ab5faf6345fbdd3c1c3d6f65d1ab76fc32d2fcae61402127ed",
    "docs/refactor/two-server/section-1/inventory/surface-bot.json": "bcd7299368f93b3e1abbc14c960d32adae591acf2a9f095279d7e77815474af7",
    "docs/refactor/two-server/section-1/inventory/surface-web.json": "37b965a4b29050acab829a1018da5889bfd4c2eb8168ea4da1b4963d764f15d8",
    "docs/refactor/two-server/section-1/inventory/surface-jobs.json": "479a9c13cb265e187394ac345fe2c6e3fbfc02a203f77d883314a2ca43c12b27",
    "docs/refactor/two-server/section-1/inventory/scenario-records.json": "036e65dc9ce96d26ccbc1704d85298c42d52f097e198192df6fd623a4895a4a5",
    "docs/refactor/two-server/section-1/inventory/recent-characterization.json": "5a235244378a63ad9da935e5e70c98593d4664f5f36cfcca3a610743aeef33eb",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation.json": "6756471cd3126794f0d7f9e4fb0995e5e395fa599cf137577d6f4e59a6a67e3c",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-2.json": "77227a80651a3cfc911450cbfcb6cce37f94ac035796739263dc77dbeb8c7e03",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-3.json": "75e090c1ef0b42c935432664a1ea30bbb2d90094df5a4f185a64bd1ff85211df",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-4.json": "38fa121e4b18991360acfae077c2d1f81b925e921e3005ceb95cfe5fb018aac5",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-5.json": "6ed554a3261009905d4364397199bcf01ec902de55c6b09b33d0770141dea806",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-6.json": "a9e3aa8d0ee7ccb51ceb070b17a47e3d61d816f69ebfe2c514417039019ffa7c",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-7.json": "d824f0ba5f2683150fca2e5a690bd0e7c67d9a4e73b8fd2d5a44c35dba78e6b3",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-8.json": "ef26e77bb8fca502b0fd3bacc5b2676c53652ad5ba5c160495de2c05d0fdb688",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-9.json": "7dd8751b91e53fb47161197a16401df8872446aad1f272c0aaa01b3e0efb82d6",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-10.json": "b4675c7c04facd4f6f22e9aa60dd2d0e7f3c2b3e08f1dabcd25f78b3f186c4db",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-11.json": "75fd2ee23c469393d7ad197ed697671179a3670a145ddbd7f7ea62e247fb8392",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json": "ebe8e3c3189f8961a61d256ed5cc690864c2e4d7f5c6d1cc91e3554060769071",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json": "80e23877c849688cddb567b88898ee1440adee0a65a07237b5325d36dcab0a6d",
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-c15-from-c17.json": "b7dac667be1584c50743b24d806d2b9d1c6fe50a85ec1e9936a65ed96754ffbb",
    "tests/test_p1_00b_characterization_trade_export_self_delegation.py": "b8db918415014c2fbaec56888bdc37e6be53f329829a6a1af9dd0ba72b5856d1",
    "tests/test_p1_00b_characterization_c16_decoded_ownership_closure_restoration.py": "eebfa10b7b7d3205f53164bf075f4bef29b22860349bfc29051babad5ad330d8",
}

C13_SEED = "API-TRADE-GET-TRADES-EXPORT-TRADES-WITH-USER-L5797"
C14_SEED = "API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"
C13_RECORD_PATH = "tests/test_p1_00b_characterization_trade_export_self_delegation.py"
C13_RECORD_SHA = "b8db918415014c2fbaec56888bdc37e6be53f329829a6a1af9dd0ba72b5856d1"
C13_RECORD_LAST_COMMIT = "98c98e93e94aa426ebae7502a67a5d5053a3a31a"
C13_TEST_SELECTOR = (
    "tests/test_p1_00b_characterization_trade_export_self_delegation.py::"
    "TradeExportSelfDelegationCharacterizationTests::"
    "test_self_target_delegates_exact_filters_and_returns_export_response"
)
COVERAGE_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-seed-coverage-387-snapshot.json"
)
RECONCILIATION_13_14_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json"
)
CONTINUATION_13_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json"
)
CONTINUATION_11_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-11.json"
)


def freeze_evidence_manifest(repo_root: Path) -> dict[str, bytes]:
    frozen: dict[str, bytes] = {}
    for relpath, expected_sha in FROZEN_HEAD_MANIFEST.items():
        file_path = repo_root / relpath
        if not file_path.is_file():
            raise AssertionError(f"Required manifest file missing: {file_path}")
        raw_bytes = file_path.read_bytes()
        actual_sha = hashlib.sha256(raw_bytes).hexdigest()
        if actual_sha != expected_sha:
            raise AssertionError(
                f"Manifest SHA mismatch for {relpath}: expected {expected_sha}, got {actual_sha}"
            )
        frozen[relpath] = raw_bytes
    return frozen


def compute_canonical_accounting(
    frozen_bytes: dict[str, bytes],
) -> tuple[dict[str, int], set[str], set[str], set[str], set[str], set[str]]:
    coverage_doc = json.loads(frozen_bytes[COVERAGE_RELPATH].decode("utf-8"))
    surface_paths = coverage_doc["inventory"]["paths"]
    surface_seeds: set[str] = set()
    per_file_counts: dict[str, int] = {}
    for sp in surface_paths:
        sdoc = json.loads(frozen_bytes[sp].decode("utf-8"))
        f_ids = {entry["behavior_id"] for entry in sdoc["entries"]}
        per_file_counts[sp] = len(f_ids)
        surface_seeds.update(f_ids)

    if len(surface_seeds) != 465:
        raise ValueError(f"Expected 465 total surface seeds, got {len(surface_seeds)}")

    evidence_sources = coverage_doc["evidence_sources"]
    baseline_path = evidence_sources["baseline_path"]
    base_doc = json.loads(frozen_bytes[baseline_path].decode("utf-8"))

    blocker_item = None
    for item in base_doc.get("blocker_registry", {}).get("items", []):
        if item.get("blocker_id") == "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE":
            blocker_item = item
            break
    if blocker_item is None:
        raise ValueError("Missing blocker_id P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE")

    reason = blocker_item["reason"]
    start_delim = "Evidenced seeds are "
    end_delim = ". Sibling family"
    s_idx = reason.find(start_delim)
    e_idx = reason.find(end_delim)
    if s_idx == -1 or e_idx == -1:
        raise ValueError(f"Could not locate delimiters in blocker reason: {reason}")
    raw_seeds = reason[s_idx + len(start_delim) : e_idx]
    baseline_seeds = {s.strip() for s in raw_seeds.split(",") if s.strip()}
    if len(baseline_seeds) != 135:
        raise ValueError(f"Expected 135 baseline seeds, got {len(baseline_seeds)}")

    cont_keys = ["base_path", "continuation_path"] + [f"continuation_{i}_path" for i in range(2, 12)]
    continuation_seeds: set[str] = set()
    for ck in cont_keys:
        cp = evidence_sources[ck]
        cdoc = json.loads(frozen_bytes[cp].decode("utf-8"))
        for rec in cdoc.get("records", []):
            continuation_seeds.update(rec.get("seed_ids", []))

    new_beyond_baseline = continuation_seeds - baseline_seeds
    if len(new_beyond_baseline) != 252:
        raise ValueError(f"Expected 252 new beyond baseline, got {len(new_beyond_baseline)}")

    evidenced_seeds = baseline_seeds | continuation_seeds
    if len(evidenced_seeds) != 387:
        raise ValueError(f"Expected 387 total evidenced seeds, got {len(evidenced_seeds)}")

    remaining_seeds = surface_seeds - evidenced_seeds
    if len(remaining_seeds) != 78:
        raise ValueError(f"Expected 78 remaining seeds, got {len(remaining_seeds)}")

    return (
        per_file_counts,
        surface_seeds,
        baseline_seeds,
        new_beyond_baseline,
        evidenced_seeds,
        remaining_seeds,
    )


def assert_c13_canonical_seed_membership(
    reconciliation_data: dict,
    frozen_evidence_bytes: dict[str, bytes],
) -> None:
    (
        _counts,
        _surface,
        _baseline,
        _new_seeds,
        evidenced_seeds,
        _remaining,
    ) = compute_canonical_accounting(frozen_evidence_bytes)
    c13_seed = C13_SEED
    if c13_seed in evidenced_seeds:
        raise ValueError(f"Inconsistent canonical evidence: C13 seed '{c13_seed}' is unexpectedly evidenced")

    seed_accounting = reconciliation_data.get("seed_accounting", {})
    prospective_seeds = seed_accounting.get("prospective_distinct_seeds", [])
    pre_reconciled_seeds = seed_accounting.get("pre_reconciled_distinct_seeds", [])

    if c13_seed in pre_reconciled_seeds:
        raise ValueError(
            f"Invalid already-covered classification for C13 seed '{c13_seed}': "
            "seed is absent from canonical evidenced seeds and must not be marked pre-reconciled"
        )
    if c13_seed not in prospective_seeds:
        raise ValueError(
            f"Missing prospective classification for C13 seed '{c13_seed}': "
            "seed must be present in prospective_distinct_seeds"
        )


def assert_closure_bytes(base_dir: Path, manifest: dict[str, bytes]) -> None:
    for relpath, expected_bytes in manifest.items():
        file_path = base_dir / relpath
        if not file_path.is_file():
            raise AssertionError(f"Missing file in closure: {file_path}")
        assert_receipt_bytes_unchanged(file_path, expected_bytes)


class C13CanonicalSeedMembershipTests(unittest.TestCase):
    def test_c13_false_covered_claim_rejected_and_complete_evidence_restored(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        frozen_bytes = freeze_evidence_manifest(repo_root)

        (
            counts,
            surface_seeds,
            baseline_seeds,
            new_seeds,
            evidenced_seeds,
            remaining_seeds,
        ) = compute_canonical_accounting(frozen_bytes)

        self.assertEqual(counts["docs/refactor/two-server/section-1/inventory/surface-api.json"], 217)
        self.assertEqual(counts["docs/refactor/two-server/section-1/inventory/surface-bot.json"], 203)
        self.assertEqual(counts["docs/refactor/two-server/section-1/inventory/surface-web.json"], 30)
        self.assertEqual(counts["docs/refactor/two-server/section-1/inventory/surface-jobs.json"], 15)
        self.assertEqual(len(surface_seeds), 465)
        self.assertEqual(len(baseline_seeds), 135)
        self.assertEqual(len(new_seeds), 252)
        self.assertEqual(len(evidenced_seeds), 387)
        self.assertEqual(len(remaining_seeds), 78)

        self.assertNotIn(C13_SEED, evidenced_seeds)
        self.assertIn(C13_SEED, surface_seeds)
        self.assertIn(C13_SEED, remaining_seeds)

        c13_doc = json.loads(frozen_bytes[CONTINUATION_13_RELPATH].decode("utf-8"))
        validate_continuation_thirteen_provenance(
            c13_doc,
            frozen_bytes[CONTINUATION_11_RELPATH],
            frozen_bytes[C13_RECORD_PATH],
        )

        self.assertEqual(EXPECTED_SCHEMA_VERSION, 1)
        self.assertEqual(EXPECTED_STAGE, "P1-00b")
        self.assertEqual(EXPECTED_PREDECESSOR_PATH, CONTINUATION_11_RELPATH)
        self.assertEqual(EXPECTED_PREDECESSOR_SHA, FROZEN_HEAD_MANIFEST[CONTINUATION_11_RELPATH])
        self.assertEqual(EXPECTED_PREDECESSOR_LAST_COMMIT, "3e8fd101e41697b6ed8cc53c8f19f11fb3cf591c")
        self.assertEqual(EXPECTED_RECORD_PATH, C13_RECORD_PATH)
        self.assertEqual(EXPECTED_RECORD_SHA, C13_RECORD_SHA)
        self.assertEqual(EXPECTED_RECORD_LAST_COMMIT, C13_RECORD_LAST_COMMIT)
        self.assertEqual(EXPECTED_SEED_IDS, [C13_SEED])
        self.assertEqual(EXPECTED_TEST_SELECTORS, [C13_TEST_SELECTOR])

        recon_doc = json.loads(frozen_bytes[RECONCILIATION_13_14_RELPATH].decode("utf-8"))
        self.assertIn(C13_SEED, recon_doc["seed_accounting"]["prospective_distinct_seeds"])
        self.assertNotIn(C13_SEED, recon_doc["seed_accounting"]["pre_reconciled_distinct_seeds"])

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for relpath, raw in frozen_bytes.items():
                dest = temp_path / relpath
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)

            assert_closure_bytes(temp_path, frozen_bytes)
            assert_closure_bytes(repo_root, frozen_bytes)

            recon_temp_path = temp_path / RECONCILIATION_13_14_RELPATH
            genuine_temp_data = json.loads(recon_temp_path.read_bytes().decode("utf-8"))
            assert_c13_canonical_seed_membership(genuine_temp_data, frozen_bytes)

            mutated_data = copy.deepcopy(genuine_temp_data)
            mutated_data["seed_accounting"]["prospective_distinct_seeds"] = [
                s
                for s in mutated_data["seed_accounting"]["prospective_distinct_seeds"]
                if s != C13_SEED
            ]
            mutated_data["seed_accounting"]["pre_reconciled_distinct_seeds"].append(C13_SEED)
            recon_temp_path.write_bytes(
                json.dumps(mutated_data, indent=2).encode("utf-8") + b"\n"
            )

            decoded_mutated = json.loads(recon_temp_path.read_bytes().decode("utf-8"))
            self.assertIn(C13_SEED, decoded_mutated["seed_accounting"]["pre_reconciled_distinct_seeds"])
            self.assertNotIn(C13_SEED, decoded_mutated["seed_accounting"]["prospective_distinct_seeds"])
            self.assertIn(C14_SEED, decoded_mutated["seed_accounting"]["pre_reconciled_distinct_seeds"])

            with self.assertRaises(ValueError) as cm:
                assert_c13_canonical_seed_membership(decoded_mutated, frozen_bytes)
            err_msg = str(cm.exception)
            self.assertIn(C13_SEED, err_msg)
            self.assertIn("already-covered", err_msg)

            assert_closure_bytes(repo_root, frozen_bytes)
            for relpath, raw in frozen_bytes.items():
                if relpath != RECONCILIATION_13_14_RELPATH:
                    assert_receipt_bytes_unchanged(temp_path / relpath, raw)

            with self.assertRaises(AssertionError):
                assert_receipt_bytes_unchanged(
                    recon_temp_path,
                    frozen_bytes[RECONCILIATION_13_14_RELPATH],
                )

            recon_temp_path.write_bytes(frozen_bytes[RECONCILIATION_13_14_RELPATH])
            assert_receipt_bytes_unchanged(
                recon_temp_path,
                frozen_bytes[RECONCILIATION_13_14_RELPATH],
            )

            restored_data = json.loads(recon_temp_path.read_bytes().decode("utf-8"))
            assert_c13_canonical_seed_membership(restored_data, frozen_bytes)
            assert_closure_bytes(temp_path, frozen_bytes)

        self.assertFalse(temp_path.exists())
        assert_closure_bytes(repo_root, frozen_bytes)

    def test_historical_reader_ignores_live_manifest_and_restores_snapshot(self) -> None:
        """Scope: 5/5 evidence reconciled, canonical integration still pending,
        pending_five=5, units_since_reconciliation=5,
        global_reconciliation_complete=false, coverage 387/465 and 78 remaining,
        24 unresolved origins, both High blockers open, P1-00 COMPLETE and P1-00b IN_PROGRESS.
        Preserve defer_prior_unit=normal169-historical-coverage-isolation-20261003-c004,
        defer_prior_unit=normal169-c16-receipt-custody-20261003-c001 and
        defer_prior_unit=normal169-c16-machine-linkage-20261003-c001-v2 verbatim.
        Exclude read_user self-success, super-admin rejection, authorization policy coverage,
        mutation, persistence, retry, reload, origin cells, seed/blocker/Stage closure and sixth behavior.
        This migration alone changes no product behavior, seed count, pending counter or Stage status.
        """
        expected_manifest_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-seed-coverage-387-snapshot.json"
        )
        expected_manifest_sha = (
            "3a2662a8e60068566582744461bb12cfc3788d3ea04857ca23f99028548f25b7"
        )
        live_manifest_path = (
            "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
        )

        repo_root = Path(__file__).resolve().parents[1]
        frozen_bytes = freeze_evidence_manifest(repo_root)

        self.assertEqual(COVERAGE_RELPATH, expected_manifest_path)
        self.assertIn(expected_manifest_path, FROZEN_HEAD_MANIFEST)
        self.assertEqual(
            FROZEN_HEAD_MANIFEST[expected_manifest_path],
            expected_manifest_sha,
        )
        self.assertIn(expected_manifest_path, frozen_bytes)
        self.assertEqual(
            hashlib.sha256(frozen_bytes[expected_manifest_path]).hexdigest(),
            expected_manifest_sha,
        )
        self.assertNotIn(live_manifest_path, FROZEN_HEAD_MANIFEST)
        self.assertNotIn(live_manifest_path, frozen_bytes)

        (
            repo_counts,
            repo_surface,
            repo_baseline,
            repo_new,
            repo_evidenced,
            repo_remaining,
        ) = compute_canonical_accounting(frozen_bytes)

        self.assertEqual(len(repo_surface), 465)
        self.assertEqual(len(repo_baseline), 135)
        self.assertEqual(len(repo_new), 252)
        self.assertEqual(len(repo_evidenced), 387)
        self.assertEqual(len(repo_remaining), 78)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            for relpath, raw in frozen_bytes.items():
                dest = temp_path / relpath
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)

            assert_closure_bytes(temp_path, frozen_bytes)
            assert_closure_bytes(repo_root, frozen_bytes)

            self.assertFalse((temp_path / live_manifest_path).exists())
            temp_snap_absent = freeze_evidence_manifest(temp_path)
            self.assertEqual(temp_snap_absent, frozen_bytes)
            (
                _c_abs,
                surface_abs,
                baseline_abs,
                new_abs,
                evidenced_abs,
                remaining_abs,
            ) = compute_canonical_accounting(temp_snap_absent)
            self.assertEqual(len(surface_abs), 465)
            self.assertEqual(len(baseline_abs), 135)
            self.assertEqual(len(new_abs), 252)
            self.assertEqual(len(evidenced_abs), 387)
            self.assertEqual(len(remaining_abs), 78)

            live_temp_file = temp_path / live_manifest_path
            live_temp_file.parent.mkdir(parents=True, exist_ok=True)
            live_temp_file.write_bytes(
                b'{"schema_version": 1, "stage": "P1-00b", "changed_canary": "nonsecret"}\n'
            )
            self.assertTrue(live_temp_file.is_file())

            temp_snap_live_changed = freeze_evidence_manifest(temp_path)
            self.assertEqual(temp_snap_live_changed, frozen_bytes)
            (
                _c_chg,
                surface_chg,
                baseline_chg,
                new_chg,
                evidenced_chg,
                remaining_chg,
            ) = compute_canonical_accounting(temp_snap_live_changed)
            self.assertEqual(len(surface_chg), 465)
            self.assertEqual(len(baseline_chg), 135)
            self.assertEqual(len(new_chg), 252)
            self.assertEqual(len(evidenced_chg), 387)
            self.assertEqual(len(remaining_chg), 78)

            self.assertEqual(temp_snap_absent, frozen_bytes)
            self.assertEqual(temp_snap_live_changed, frozen_bytes)
            assert_closure_bytes(repo_root, frozen_bytes)

            hist_temp_file = temp_path / expected_manifest_path
            original_hist_bytes = frozen_bytes[expected_manifest_path]
            drifted_hist_bytes = original_hist_bytes + b"\n"
            hist_temp_file.write_bytes(drifted_hist_bytes)

            decoded_original = json.loads(original_hist_bytes.decode("utf-8"))
            decoded_drifted = json.loads(drifted_hist_bytes.decode("utf-8"))
            self.assertEqual(decoded_drifted, decoded_original)

            with self.assertRaises(AssertionError) as cm:
                freeze_evidence_manifest(temp_path)
            err_msg = str(cm.exception)
            self.assertIn(expected_manifest_path, err_msg)
            self.assertIn("Manifest SHA mismatch", err_msg)

            for relpath, raw in frozen_bytes.items():
                if relpath != expected_manifest_path:
                    assert_receipt_bytes_unchanged(temp_path / relpath, raw)
            assert_closure_bytes(repo_root, frozen_bytes)

            hist_temp_file.write_bytes(original_hist_bytes)
            assert_receipt_bytes_unchanged(hist_temp_file, original_hist_bytes)
            restored_snap = freeze_evidence_manifest(temp_path)
            self.assertEqual(restored_snap, frozen_bytes)
            (
                _c_res,
                surface_res,
                baseline_res,
                new_res,
                evidenced_res,
                remaining_res,
            ) = compute_canonical_accounting(restored_snap)
            self.assertEqual(len(surface_res), 465)
            self.assertEqual(len(baseline_res), 135)
            self.assertEqual(len(new_res), 252)
            self.assertEqual(len(evidenced_res), 387)
            self.assertEqual(len(remaining_res), 78)
            assert_closure_bytes(temp_path, frozen_bytes)

        self.assertFalse(temp_path.exists())
        assert_closure_bytes(repo_root, frozen_bytes)


if __name__ == "__main__":
    unittest.main()
