"""Focused no-I/O tests for the bounded Release-0 candidate selection seam."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import unittest
from unittest.mock import patch

import core.release0_candidate_selection as selection


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _Inspector:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls = 0

    def inspect_source(self):
        self.calls += 1
        return self.value


class _Reader:
    def __init__(self, contents: dict[str, bytes]) -> None:
        self.contents = contents
        self.calls: list[str] = []
        self.owner_uid = 0
        self.mode = 0o644
        self.regular_file = True
        self.symlink = False
        self.stable = True

    def read_file(self, *, relative_path: str):
        self.calls.append(relative_path)
        return selection.Release0CandidateFileObservation(
            relative_path=relative_path,
            owner_uid=self.owner_uid,
            mode=self.mode,
            regular_file=self.regular_file,
            symlink=self.symlink,
            stable=self.stable,
            content=self.contents[relative_path],
        )


class Release0CandidateSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_contents = {
            "core/a_release0.py": b"release0-writer-term\n",
            "scripts/b_release0.py": b"release0-dark-standby\n",
        }
        self.fixture_specs = (
            selection._Release0PathSpec(
                group="writer-term-safety",
                relative_path="core/a_release0.py",
                expected_sha256=_digest(self.fixture_contents["core/a_release0.py"]),
                expected_bytes=len(self.fixture_contents["core/a_release0.py"]),
            ),
            selection._Release0PathSpec(
                group="dark-standby-preflight",
                relative_path="scripts/b_release0.py",
                expected_sha256=_digest(self.fixture_contents["scripts/b_release0.py"]),
                expected_bytes=len(self.fixture_contents["scripts/b_release0.py"]),
            ),
        )
        self.source_inspection = selection.Release0CandidateSourceInspection(
            schema=selection.RELEASE0_CANDIDATE_INVENTORY_SCHEMA
            + "/source-inspection-v1",
            status="audited-digest-locked-source",
            baseline_release_sha=selection.FIXED_RELEASE0_CANDIDATE_BASELINE_SHA,
            baseline_git_tree_id=selection.FIXED_RELEASE0_CANDIDATE_BASELINE_TREE,
            stable=True,
            root_owned=True,
            source_no_follow=True,
        )
        self.target_inspection = selection.Release0CandidateTargetInspection(
            schema=selection.RELEASE0_CANDIDATE_MATERIALIZATION_PLAN_SCHEMA
            + "/target-inspection-v1",
            status="clean-fixed-baseline-target",
            baseline_release_sha=selection.FIXED_RELEASE0_CANDIDATE_BASELINE_SHA,
            baseline_git_tree_id=selection.FIXED_RELEASE0_CANDIDATE_BASELINE_TREE,
            clean=True,
            stable=True,
            root_owned=True,
            target_no_follow=True,
        )

    def build_fixture_inventory(self):
        source = _Inspector(self.source_inspection)
        reader = _Reader(dict(self.fixture_contents))
        with patch.object(
            selection,
            "_RELEASE0_CANDIDATE_PATH_SPECS",
            self.fixture_specs,
        ):
            inventory = selection.build_release0_candidate_inventory(
                config=selection.Release0CandidateInventoryConfig(enabled=True),
                source_inspector=source,
                file_reader=reader,
            )
        return inventory, source, reader

    def test_audited_profile_is_literal_and_not_a_checkpoint_import(self) -> None:
        self.assertEqual(
            (
                "core/application_writer_term.py",
                "core/external_effect_execution_gate.py",
                "core/webapp_ir_dark_snapshot_preflight.py",
                "scripts/preflight_fenced_fi_writer.py",
                "scripts/preflight_webapp_ir_dark_snapshot_standby.py",
            ),
            selection.RELEASE0_CANDIDATE_SELECTED_PATHS,
        )
        self.assertEqual(
            {
                "core/application_writer_term.py": (
                    "000cc65c4ef1bf77e68e9f59be4d77b255ce3d26f0d066fbf7a1c191c29b1a6d",
                    10313,
                ),
                "core/external_effect_execution_gate.py": (
                    "c4bc72f956a684b9b7063a874f46393b6dc9bbe172c55ff3088e14e8ed57f082",
                    35581,
                ),
                "core/webapp_ir_dark_snapshot_preflight.py": (
                    "52148650f5d7f1f5c37b0b6724499f0fd17869d121ad053bf18107b4b2b9c926",
                    8134,
                ),
                "scripts/preflight_fenced_fi_writer.py": (
                    "009f8ef337d43cb099bc173e888761bbff19700c92ed9166401ad4ec1fd19ba8",
                    38160,
                ),
                "scripts/preflight_webapp_ir_dark_snapshot_standby.py": (
                    "2976ad7bc07a9964dbfa72b5a02c02d589797a53677c9caf6fe3f37794ce0ed4",
                    9773,
                ),
            },
            {
                spec.relative_path: (spec.expected_sha256, spec.expected_bytes)
                for spec in selection._RELEASE0_CANDIDATE_PATH_SPECS
            },
        )
        self.assertEqual(
            "be29acf7d8d32618b247c7865fd5506c7f9dc51d2e2c78ca3f2d7eb074e75146",
            selection.RELEASE0_CANDIDATE_SELECTION_PROFILE_SHA256,
        )
        self.assertEqual(
            selection.RELEASE0_CANDIDATE_SELECTION_PROFILE_SHA256,
            selection._selection_profile_sha256(),
        )
        self.assertRegex(selection.RELEASE0_CANDIDATE_SELECTION_PROFILE_SHA256, r"^[0-9a-f]{64}$")

    def test_classification_is_fail_closed_for_forbidden_and_unknown_families(self) -> None:
        expected = {
            "core/application_writer_term.py": "selected",
            "core/physical_full_matrix_v4_execution_driver.py": "forbidden-full-matrix-v4",
            "core/physical_wal_v2r_witness_roundtrip_contract.py": "forbidden-full-matrix-v2r",
            "migrations/experimental/0v4p1ack01_add_checkpoint.py": "forbidden-experimental",
            "core/physical_wa_ir_postgres_failback_capture_bridge.py": "forbidden-retired",
            "core/physical_operational_failover_v1.py": "forbidden-review-only",
            "main.py": "not-selected",
            "../outside.py": "invalid",
        }
        for path, disposition in expected.items():
            with self.subTest(path=path):
                self.assertEqual(disposition, selection.classify_release0_candidate_path(path))

    def test_inventory_freezes_only_exact_selected_bytes_and_is_non_authorizing(self) -> None:
        inventory, source, reader = self.build_fixture_inventory()
        expected_paths = tuple(self.fixture_contents)
        self.assertEqual(1, source.calls)
        self.assertEqual(list(expected_paths), reader.calls)
        self.assertEqual(expected_paths, tuple(entry.relative_path for entry in inventory.entries))
        self.assertFalse(inventory.release_authorized)
        self.assertFalse(inventory.deployment_authorized)
        self.assertFalse(inventory.full_matrix_authorized)
        with patch.object(selection, "_RELEASE0_CANDIDATE_PATH_SPECS", self.fixture_specs):
            self.assertEqual(inventory, selection.parse_release0_candidate_inventory(inventory.canonical_manifest))
            self.assertEqual(inventory, selection.verify_release0_candidate_inventory(inventory))
        self.assertNotIn(b"1a07b9df", inventory.canonical_manifest)
        self.assertNotIn(b"ssh", inventory.canonical_manifest.lower())

    def test_disabled_or_unsafe_source_or_digest_drift_refuses_before_any_plan(self) -> None:
        source = _Inspector(self.source_inspection)
        reader = _Reader(dict(self.fixture_contents))
        with patch.object(selection, "_RELEASE0_CANDIDATE_PATH_SPECS", self.fixture_specs):
            with self.assertRaisesRegex(selection.Release0CandidateError, "RELEASE0_CANDIDATE_DISABLED"):
                selection.build_release0_candidate_inventory(
                    config=selection.Release0CandidateInventoryConfig(enabled=False),
                    source_inspector=source,
                    file_reader=reader,
                )
            self.assertEqual([], reader.calls)

            source.value = replace(self.source_inspection, stable=False)
            with self.assertRaisesRegex(selection.Release0CandidateError, "RELEASE0_SOURCE_INSPECTION_REJECTED"):
                selection.build_release0_candidate_inventory(
                    config=selection.Release0CandidateInventoryConfig(enabled=True),
                    source_inspector=source,
                    file_reader=reader,
                )
            self.assertEqual([], reader.calls)

            source.value = self.source_inspection
            reader.contents["core/a_release0.py"] = b"changed"
            with self.assertRaisesRegex(selection.Release0CandidateError, "RELEASE0_SOURCE_FILE_SIZE_MISMATCH|RELEASE0_SOURCE_FILE_DIGEST_MISMATCH"):
                selection.build_release0_candidate_inventory(
                    config=selection.Release0CandidateInventoryConfig(enabled=True),
                    source_inspector=source,
                    file_reader=reader,
                )

    def test_non_root_or_symlinked_source_and_dirty_target_refuse(self) -> None:
        source = _Inspector(self.source_inspection)
        reader = _Reader(dict(self.fixture_contents))
        with patch.object(selection, "_RELEASE0_CANDIDATE_PATH_SPECS", self.fixture_specs):
            reader.owner_uid = 1000
            with self.assertRaisesRegex(selection.Release0CandidateError, "RELEASE0_SOURCE_FILE_UNSAFE"):
                selection.build_release0_candidate_inventory(
                    config=selection.Release0CandidateInventoryConfig(enabled=True),
                    source_inspector=source,
                    file_reader=reader,
                )

            reader.owner_uid = 0
            reader.symlink = True
            with self.assertRaisesRegex(selection.Release0CandidateError, "RELEASE0_SOURCE_FILE_UNSAFE"):
                selection.build_release0_candidate_inventory(
                    config=selection.Release0CandidateInventoryConfig(enabled=True),
                    source_inspector=source,
                    file_reader=reader,
                )

            reader.symlink = False
            inventory = selection.build_release0_candidate_inventory(
                config=selection.Release0CandidateInventoryConfig(enabled=True),
                source_inspector=source,
                file_reader=reader,
            )
            with self.assertRaisesRegex(selection.Release0CandidateError, "RELEASE0_TARGET_INSPECTION_REJECTED"):
                selection.prepare_release0_candidate_materialization(
                    inventory=inventory,
                    target_inspection=replace(self.target_inspection, clean=False),
                    enabled=True,
                )

    def test_parser_rejects_noncanonical_and_tampered_entries(self) -> None:
        inventory, _source, _reader = self.build_fixture_inventory()
        raw = json.loads(inventory.canonical_manifest)
        raw["entries"][0]["sha256"] = "a" * 64
        tampered = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        with patch.object(selection, "_RELEASE0_CANDIDATE_PATH_SPECS", self.fixture_specs):
            with self.assertRaisesRegex(selection.Release0CandidateError, "RELEASE0_INVENTORY_ENTRY_MISMATCH"):
                selection.parse_release0_candidate_inventory(tampered)
            forged = replace(inventory, manifest_sha256="0" * 64)
            with self.assertRaisesRegex(selection.Release0CandidateError, "RELEASE0_INVENTORY_TAMPERED"):
                selection.verify_release0_candidate_inventory(forged)

    def test_plan_and_readback_require_exact_path_set_and_exact_bytes(self) -> None:
        inventory, _source, _reader = self.build_fixture_inventory()
        target_reader = _Reader(dict(self.fixture_contents))
        expected_paths = tuple(self.fixture_contents)
        observation = selection.Release0CandidateTargetOverlayObservation(
            schema=selection.RELEASE0_CANDIDATE_READBACK_RECEIPT_SCHEMA + "/target-overlay-v1",
            status="exact-release0-foundation-overlay-observed",
            baseline_release_sha=selection.FIXED_RELEASE0_CANDIDATE_BASELINE_SHA,
            baseline_git_tree_id=selection.FIXED_RELEASE0_CANDIDATE_BASELINE_TREE,
            stable=True,
            root_owned=True,
            target_no_follow=True,
            complete_changed_path_observation=True,
            changed_paths=expected_paths,
            target_git_commit_created=False,
            release_seal_created=False,
        )
        with patch.object(selection, "_RELEASE0_CANDIDATE_PATH_SPECS", self.fixture_specs):
            with self.assertRaisesRegex(selection.Release0CandidateError, "RELEASE0_MATERIALIZATION_DISABLED"):
                selection.prepare_release0_candidate_materialization(
                    inventory=inventory,
                    target_inspection=self.target_inspection,
                )
            plan = selection.prepare_release0_candidate_materialization(
                inventory=inventory,
                target_inspection=self.target_inspection,
                enabled=True,
            )
            receipt = selection.verify_release0_candidate_materialization_readback(
                inventory=inventory,
                plan=plan,
                target_observation=observation,
                target_file_reader=target_reader,
            )
            self.assertEqual(list(expected_paths), target_reader.calls)
            self.assertEqual(expected_paths, receipt.observed_changed_paths)
            self.assertFalse(receipt.release_authorized)
            self.assertFalse(receipt.deployment_authorized)
            self.assertFalse(receipt.full_matrix_authorized)

            target_reader.contents["scripts/b_release0.py"] = b"wrong"
            with self.assertRaisesRegex(selection.Release0CandidateError, "RELEASE0_SOURCE_FILE_SIZE_MISMATCH|RELEASE0_SOURCE_FILE_DIGEST_MISMATCH"):
                selection.verify_release0_candidate_materialization_readback(
                    inventory=inventory,
                    plan=plan,
                    target_observation=observation,
                    target_file_reader=target_reader,
                )

            wrong_paths = replace(observation, changed_paths=expected_paths + ("main.py",))
            with self.assertRaisesRegex(selection.Release0CandidateError, "RELEASE0_TARGET_OVERLAY_REJECTED"):
                selection.verify_release0_candidate_materialization_readback(
                    inventory=inventory,
                    plan=plan,
                    target_observation=wrong_paths,
                    target_file_reader=_Reader(dict(self.fixture_contents)),
                )

    def test_revalidation_before_a_separate_copier_rechecks_every_byte(self) -> None:
        inventory, _source, _reader = self.build_fixture_inventory()
        source = _Inspector(self.source_inspection)
        reader = _Reader(dict(self.fixture_contents))
        with patch.object(selection, "_RELEASE0_CANDIDATE_PATH_SPECS", self.fixture_specs):
            selection.verify_release0_candidate_source(
                inventory=inventory,
                source_inspector=source,
                file_reader=reader,
            )
        self.assertEqual(list(self.fixture_contents), reader.calls)


if __name__ == "__main__":
    unittest.main()
