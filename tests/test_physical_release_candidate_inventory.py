"""Focused tests for the non-materializing physical release inventory."""

from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

import core.physical_release_candidate_inventory as inventory


ROOT = Path("/srv/trading-bot-three-site/review-source")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _core_import_paths(relative_path: str) -> frozenset[str]:
    """Return direct absolute ``core`` module imports as source paths.

    The reviewed V2/V4 sets are intentionally literal allow-lists.  This small
    static check makes a new direct dependency visible in the release audit
    instead of allowing a materialized runtime to import an unreviewed or
    retired activation module at execution time.
    """

    tree = ast.parse(
        (PROJECT_ROOT / relative_path).read_text(encoding="utf-8"),
        filename=relative_path,
    )
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("core."):
                    imported.add(
                        "core/" + alias.name.removeprefix("core.").replace(".", "/") + ".py"
                    )
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module is not None and node.module.startswith("core."):
                imported.add(
                    "core/" + node.module.removeprefix("core.").replace(".", "/") + ".py"
                )
            elif node.module == "core":
                for alias in node.names:
                    imported.add("core/" + alias.name.replace(".", "/") + ".py")
    return frozenset(imported)


def source_object() -> inventory.PhysicalReleaseCandidateSourceObject:
    return inventory.PhysicalReleaseCandidateSourceObject(
        path=ROOT,
        owner_uid=0,
        mode=0o750,
        directory=True,
        symlink=False,
        ancestors_root_controlled=True,
    )


class _Inspector:
    def __init__(self, *, clean: bool = False) -> None:
        self.value = inventory.PhysicalReleaseCandidateSourceInspection(
            source_root=source_object(),
            release_sha=inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA,
            git_tree_id=inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE,
            clean=clean,
            stable=True,
        )
        self.calls = 0

    def inspect_source(self, *, source_root: Path):
        self.calls += 1
        self.last_path = source_root
        return self.value


class _Reader:
    def __init__(self) -> None:
        self.overrides: dict[str, inventory.PhysicalReleaseCandidateFileObservation] = {}
        self.calls: list[str] = []

    def read_file(self, *, source_root: Path, relative_path: str):
        self.calls.append(relative_path)
        if relative_path in self.overrides:
            return self.overrides[relative_path]
        body = ("reviewed:" + relative_path).encode("ascii")
        return inventory.PhysicalReleaseCandidateFileObservation(
            relative_path=relative_path,
            owner_uid=0,
            mode=0o755 if relative_path.endswith(".sh") else 0o644,
            regular_file=True,
            symlink=False,
            stable=True,
            content=body,
        )


class PhysicalReleaseCandidateInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inspector = _Inspector(clean=False)
        self.reader = _Reader()

    def config(self, **changes: object):
        values: dict[str, object] = {
            "source_root": ROOT,
            "expected_baseline_sha": inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA,
            "expected_baseline_tree": inventory.FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE,
            "enabled": True,
            "allow_dirty_staging_source": True,
        }
        values.update(changes)
        return inventory.PhysicalReleaseCandidateInventoryConfig(**values)

    def build(self, **changes: object):
        with patch.object(inventory.os, "geteuid", return_value=0):
            return inventory.build_physical_release_candidate_inventory(
                config=self.config(**changes),
                source_inspector=self.inspector,
                file_reader=self.reader,
            )

    def test_literal_runtime_selection_includes_active_foundations_and_excludes_retired_activation(self) -> None:
        self.assertEqual(330, len(inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS))
        self.assertIn(
            (
                "physical-postgres-and-promotion",
                "deploy/physical-postgres/standby-pg_hba.conf.template",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "preflight-and-release-controls",
                "scripts/verify_fenced_fi_release_identity.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "preflight-and-release-controls",
                "scripts/preflight_webapp_ir_dark_snapshot_standby.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "preflight-and-release-controls",
                "core/webapp_ir_dark_snapshot_preflight.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "preflight-and-release-controls",
                "core/fenced_fi_release_identity_runtime_binding.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "physical-postgres-and-promotion",
                "core/promotion_p0_continuity_preflight.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "preflight-and-release-controls",
                "core/physical_release_candidate_writer_quiescence_receipt.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "v4-witness-execution-boundary",
                "core/physical_full_matrix_v4_fi_fence_scope_installation_provenance.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "preflight-and-release-controls",
                "core/physical_release_seal_local_inspection_adapter.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertEqual(
            94,
            sum(
                1
                for group, _path in inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS
                if group == "object-storage-and-wal"
            ),
        )
        for path in inventory.RETIRED_PAIRED_ARVAN_S3_RUNTIME_PATHS:
            with self.subTest(retired_path=path):
                self.assertNotIn(
                    ("object-storage-and-wal", path),
                    inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
                )
        for path in inventory.RETIRED_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_PATHS:
            with self.subTest(retired_v1_path=path):
                self.assertNotIn(
                    ("object-storage-and-wal", path),
                    inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
                )
                self.assertNotIn(
                    ("physical-postgres-and-promotion", path),
                    inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
                )
        for path in inventory.V1_SINGLE_OBJECT_BASE_BACKUP_COMPATIBILITY_ONLY_PATHS:
            with self.subTest(compatibility_path=path):
                self.assertIn(
                    ("object-storage-and-wal", path),
                    inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
                )
        expected_active_operational_v1_paths = frozenset(
            {
                "core/application_writer_transaction_envelope_guard.py",
                "core/fenced_fi_release_identity.py",
                "core/physical_operational_failover_v1.py",
                "core/physical_operational_failover_v1_witness_ledger.py",
                "core/physical_operational_failover_v1_witness_ledger_durable_cas.py",
                "core/physical_operational_failover_v1_witness_term_issuer.py",
                "core/physical_operational_failover_v1_witness_term_replay_guard.py",
                "core/physical_operational_failover_v1_witness_term_revalidator.py",
                "core/physical_operational_failover_v1_v2_writer_term_bridge.py",
                "core/physical_operational_failover_v1_v2_writer_term_bridge_runtime_issuer.py",
                "core/physical_operational_failover_v1_writer_admission.py",
                "core/physical_operational_failover_v1_writer_admission_durable_state.py",
                "core/physical_operational_failover_v1_writer_admission_postgres_contract.py",
                "core/physical_operational_failover_v1_writer_admission_sqlalchemy_transaction.py",
                "core/physical_operational_failover_v1_writer_transaction_envelope.py",
                "core/production_writer_lease.py",
                "migrations/versions/0writeradm01_add_operational_writer_admission_schema.py",
                "models/operational_writer_admission.py",
            }
        )
        self.assertEqual(
            expected_active_operational_v1_paths,
            inventory.ACTIVE_OPERATIONAL_V1_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        for path in expected_active_operational_v1_paths:
            with self.subTest(active_operational_v1_path=path):
                self.assertIn(
                    ("writer-fencing-runtime", path),
                    inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
                )
        expected_active_v2_paths = frozenset(
            {
                "core/physical_arvan_s3_four_role_immutability_live_probe_runtime.py",
                "core/physical_arvan_s3_four_role_immutability_role_local_collector.py",
                "core/physical_arvan_s3_four_role_immutability_witness_dispatch_ledger.py",
                "core/physical_arvan_s3_four_role_immutability_witness_orchestration.py",
                "core/physical_arvan_s3_four_role_immutability_witness_role_agent.py",
                "core/physical_arvan_s3_four_role_immutability_preflight.py",
                "core/physical_arvan_s3_four_role_live_iam_durable_admission_bridge.py",
                "core/physical_arvan_s3_four_role_live_iam_evidence.py",
                "core/physical_arvan_s3_four_role_live_iam_preflight_gate.py",
                "core/physical_arvan_s3_four_role_live_iam_witness_ledger_runtime.py",
                "core/physical_arvan_s3_four_role_preflight_binding.py",
                "core/physical_full_matrix_v2_gen2_witnessed_ack_chain.py",
                "core/physical_full_matrix_v2_gen2_witnessed_campaign_readiness.py",
                "core/physical_full_matrix_v2_recovery_evidence.py",
                "core/physical_full_matrix_v2_witnessed_ack_chain.py",
                "core/physical_full_matrix_v2_witnessed_campaign_readiness.py",
                "core/physical_postgres_chunked_base_backup_recovery_preflight.py",
                "core/physical_postgres_chunked_base_backup_recovery_readback_attestation.py",
                "core/physical_postgres_chunked_base_backup_target_recovery_preflight.py",
                "core/physical_wal_chunked_base_backup_handoff_receipt.py",
                "core/physical_wal_chunked_base_backup_lineage_envelope.py",
                "core/physical_wal_chunked_base_backup_blob_frontier_coverage.py",
                "core/physical_wal_chunked_base_backup_manifest.py",
                "core/physical_wal_chunked_base_backup_publisher_runtime.py",
                "core/physical_wal_chunked_base_backup_receiver_receipt_ledger.py",
                "core/physical_wal_chunked_base_backup_receiver_staging_runtime.py",
                "core/physical_wal_chunked_base_backup_recovery_admission.py",
                "core/physical_wal_chunked_base_backup_remote_ack_bridge.py",
                "core/physical_wal_chunked_base_backup_resume_admission.py",
                "core/physical_wal_chunked_base_backup_target_wal_continuity.py",
                "core/physical_wal_chunked_base_backup_transfer.py",
                "core/physical_wal_v2_remote_ack.py",
                "core/physical_wal_v2_remote_ack_coverage.py",
                "core/physical_wal_v2_remote_ack_receiver_ledger.py",
                "core/physical_wal_v2_strict_remote_ack_writer_response.py",
                "core/physical_wal_v2_witness_roundtrip_contract.py",
                "core/physical_wal_v2_witness_roundtrip_delivery_contract.py",
                "core/physical_wal_v2_witness_roundtrip_delivery_runtime.py",
                "core/physical_wal_v2_witness_roundtrip_arvan_s3v4_delivery_dispatcher.py",
                "core/physical_wal_v2_witness_roundtrip_full_bundle_issuer.py",
                "core/physical_wal_v2_witness_roundtrip_full_bundle_deployment_reference.py",
                "core/physical_wal_v2_witness_roundtrip_arvan_s3v4_scope.py",
                "core/physical_wal_v2_witness_roundtrip_deployment_plan.py",
                "core/physical_wal_v2_witness_roundtrip_mailbox_admission.py",
                "core/physical_wal_v2_witness_roundtrip_s3_mailbox_adapter.py",
                "core/physical_wal_v2_witness_roundtrip_source_outbox.py",
                "core/physical_wal_v2_witness_roundtrip_strict_writer_bound_response.py",
                "core/physical_wal_v2_witness_roundtrip_strict_writer_bound_sqlalchemy_transaction.py",
                "core/physical_wal_v2_witness_roundtrip_strict_writer_bound_transaction_envelope.py",
                "core/physical_wal_v2_witness_roundtrip_strict_writer_response.py",
                "core/physical_wal_v2_witness_roundtrip_witness_ledger.py",
                "migrations/versions/0v2strictdb01_add_v2_witness_strict_writer_schema.py",
                "migrations/versions/0v2strictbind01_add_v2_witness_bound_writer_schema.py",
                "migrations/versions/0v2consreg01_add_v2_witness_attestation_consumption_registry.py",
                "migrations/versions/0v2basepin01_add_v2_gen2_base_pin_columns.py",
                "models/physical_wal_v2_witness_roundtrip_strict_writer.py",
                "models/physical_wal_v2_witness_roundtrip_strict_writer_bound.py",
                "models/physical_wal_v2_witness_roundtrip_attestation_consumption.py",
            }
        )
        self.assertEqual(
            expected_active_v2_paths,
            inventory.ACTIVE_V2_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        for path in expected_active_v2_paths:
            with self.subTest(active_v2_path=path):
                self.assertIn(
                    ("object-storage-and-wal", path),
                    inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
                )
        expected_active_v4_paths = frozenset(
            {
                "core/physical_full_matrix_execution_driver_v4.py",
                "core/physical_full_matrix_v4_final_convergence_admission.py",
                "core/physical_full_matrix_v4_materialization_preflight.py",
                "core/physical_full_matrix_v4_phase1_post_effect_strict_ack_boundary.py",
                "core/physical_full_matrix_v4_phase1_strict_ack_provenance.py",
                "core/physical_full_matrix_v4_phase3_recovery_admission.py",
                "core/physical_full_matrix_v4_phase6_fd_only_rebuild_binder.py",
                "core/physical_full_matrix_v4_phase6_failback_rebuild_admission.py",
                "core/physical_full_matrix_v4_phase_installation_provenance.py",
                "core/physical_full_matrix_v4_root_composition.py",
                "core/physical_full_matrix_v4_plan_rehydration.py",
                "core/physical_full_matrix_v4_receipt_journal.py",
                "core/physical_full_matrix_v4_retired_fi_predecessor_fence.py",
                "core/physical_full_matrix_v4_retired_fi_predecessor_fence_runtime.py",
                "core/physical_full_matrix_v4_witness_anchor_adapter.py",
                "core/physical_full_matrix_v4_witness_anchor_fi_witness_anti_replay_registry.py",
                "core/physical_full_matrix_v4_witness_anchor_fi_witness_mailbox.py",
                "core/physical_full_matrix_v4_witness_anchor_ledger.py",
                "core/physical_full_matrix_v4_witness_anchor_wire.py",
                "core/physical_full_matrix_v4_witness_successor_transition_evidence.py",
                "core/physical_full_matrix_v4_witness_successor_transition_runtime.py",
            }
        )
        self.assertEqual(
            expected_active_v4_paths,
            inventory.ACTIVE_V4_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertNotIn(
            "core/physical_full_matrix_v4_fi_fence_scope_installation_provenance.py",
            inventory.ACTIVE_V4_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        for path in (
            "core/physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint.py",
            "migrations/versions/0v4p1ack01_add_v4_phase1_post_effect_strict_ack_checkpoint.py",
            "migrations/experimental/0v4p1ack01_add_v4_phase1_post_effect_strict_ack_checkpoint.py",
            "models/physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint.py",
        ):
            with self.subTest(quarantined_phase1_checkpoint_path=path):
                self.assertNotIn(
                    path,
                    {
                        selected_path
                        for _group, selected_path in inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS
                    },
                )
        self.assertNotIn(
            "core/physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint.py",
            inventory.ACTIVE_V4_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertNotIn(
            "models/physical_full_matrix_v4_phase1_post_effect_strict_ack_checkpoint.py",
            inventory.ACTIVE_V2_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertNotIn(
            "migrations/versions/0v4p1ack01_add_v4_phase1_post_effect_strict_ack_checkpoint.py",
            inventory.ACTIVE_V2_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "v4-phase6-reverse-bundle-descriptor-binding-foundation",
                "core/physical_full_matrix_v4_phase6_reverse_bundle_descriptor_binding.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertNotIn(
            "core/physical_full_matrix_v4_phase6_reverse_bundle_descriptor_binding.py",
            inventory.ACTIVE_V4_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        for path in (
            "core/physical_full_matrix_v4_phase6_source_fd_attestation.py",
            "core/physical_full_matrix_v4_phase6_reconstruction_handoff.py",
        ):
            with self.subTest(reviewed_phase6_fd_handoff_path=path):
                self.assertIn(
                    ("v4-phase6-reverse-bundle-descriptor-binding-foundation", path),
                    inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
                )
                self.assertNotIn(path, inventory.ACTIVE_V4_PHYSICAL_RELEASE_CANDIDATE_PATHS)
        for path in expected_active_v4_paths:
            with self.subTest(active_v4_path=path):
                self.assertIn(
                    ("v4-witness-execution-boundary", path),
                    inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
                )
        for path in (
            "core/physical_arvan_s3_failback_route_commitment.py",
            "core/physical_arvan_s3_fi_publisher_role_factory.py",
            "core/physical_arvan_s3_four_role_live_iam_durable_admission_bridge.py",
            "core/physical_arvan_s3_four_role_live_iam_evidence.py",
            "core/physical_arvan_s3_four_role_live_iam_preflight_gate.py",
            "core/physical_arvan_s3_four_role_live_iam_witness_ledger_runtime.py",
            "core/physical_arvan_s3_four_role_preflight_binding.py",
            "core/physical_arvan_s3_ir_receiver_role_loader.py",
            "core/physical_arvan_s3_role_local_client_support.py",
            "core/physical_arvan_s3_role_local_credential_reader.py",
            "core/physical_arvan_s3_role_local_identity.py",
            "core/physical_arvan_s3_role_local_route_policy.py",
            "core/physical_arvan_s3_role_profiles.py",
        ):
            with self.subTest(path=path):
                self.assertIn(
                    ("object-storage-and-wal", path),
                    inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
                )
        self.assertIn(
            (
                "object-storage-and-wal",
                "core/physical_ir_to_fi_object_storage_failback_preflight.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "preflight-and-release-controls",
                "core/physical_full_matrix_receipt_journal.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "preflight-and-release-controls",
                "core/dedicated_host_preflight_ir_witness_attestation.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        for path in (
            "core/dedicated_host_preflight_receipt_agent_installation.py",
            "core/dedicated_host_preflight_witness_attestation_runtime.py",
            "scripts/install_dedicated_host_preflight_receipt_agent.py",
            "scripts/run_dedicated_host_preflight_witness_evidence_dispatcher.py",
            "scripts/run_dedicated_host_preflight_witness_evidence_root_collector.py",
        ):
            with self.subTest(preflight_installation_path=path):
                self.assertIn(
                    ("preflight-and-release-controls", path),
                    inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
                )
        self.assertIn(
            (
                "preflight-and-release-controls",
                "core/dedicated_host_preflight_ir_witness_attestation_runtime.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "preflight-and-release-controls",
                "core/dedicated_host_preflight_witness_attestation_ledger.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "preflight-and-release-controls",
                "core/dedicated_host_preflight_fi_request_provisioning_runtime.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )
        self.assertIn(
            (
                "preflight-and-release-controls",
                "core/dedicated_host_preflight_ir_request_provisioning_runtime.py",
            ),
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
        )

    def test_active_v1_v2_and_v4_static_core_imports_are_reviewed_and_never_retired(self) -> None:
        selected_paths = frozenset(
            path for _group, path in inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS
        )
        retired_paths = (
            inventory.RETIRED_PAIRED_ARVAN_S3_RUNTIME_PATHS
            | inventory.RETIRED_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_PATHS
        )
        for path in sorted(inventory.ACTIVE_OPERATIONAL_V1_PHYSICAL_RELEASE_CANDIDATE_PATHS):
            with self.subTest(active_operational_v1_path=path):
                imported_paths = _core_import_paths(path)
                self.assertFalse(imported_paths & retired_paths)
                self.assertTrue(
                    imported_paths <= selected_paths,
                    msg=f"{path} imports unreviewed paths: {sorted(imported_paths - selected_paths)}",
                )
        for path in sorted(inventory.ACTIVE_V2_PHYSICAL_RELEASE_CANDIDATE_PATHS):
            with self.subTest(active_v2_path=path):
                imported_paths = _core_import_paths(path)
                self.assertFalse(imported_paths & retired_paths)
                self.assertTrue(
                    imported_paths <= selected_paths,
                    msg=f"{path} imports unreviewed paths: {sorted(imported_paths - selected_paths)}",
                )
        for path in sorted(inventory.ACTIVE_V4_PHYSICAL_RELEASE_CANDIDATE_PATHS):
            with self.subTest(active_v4_path=path):
                imported_paths = _core_import_paths(path)
                self.assertFalse(imported_paths & retired_paths)
                self.assertFalse(
                    imported_paths
                    & inventory.V4_WITNESS_EXECUTION_LEGACY_FORBIDDEN_PATHS
                )
                self.assertTrue(
                    imported_paths <= selected_paths,
                    msg=f"{path} imports unreviewed paths: {sorted(imported_paths - selected_paths)}",
                )

    def test_canonical_complete_inventory_is_non_authorizing_and_deterministic(self) -> None:
        result = self.build()
        parsed = inventory.parse_physical_release_candidate_inventory(
            result.canonical_manifest
        )
        self.assertEqual(
            inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS,
            tuple((entry.group, entry.relative_path) for entry in result.entries),
        )
        self.assertEqual(result.manifest_sha256, parsed.manifest_sha256)
        self.assertTrue(result.source_dirty_at_capture)
        self.assertFalse(result.materialization_authorized)
        self.assertFalse(result.release_authorized)
        self.assertFalse(result.execution_authorized)
        self.assertEqual(2, self.inspector.calls)
        self.assertEqual(
            len(inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS),
            len(self.reader.calls),
        )
        self.assertNotIn(b"/srv/", result.canonical_manifest)
        self.assertNotIn(b"reviewed:", result.canonical_manifest)

    def test_disabled_nonroot_and_dirty_strict_mode_fail_before_source_read(self) -> None:
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_INVENTORY_DISABLED",
        ):
            self.build(enabled=False)
        self.assertEqual(0, self.inspector.calls)
        self.assertEqual([], self.reader.calls)

        with patch.object(inventory.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_INVENTORY_ROOT_RUNTIME_REQUIRED",
        ):
            inventory.build_physical_release_candidate_inventory(
                config=self.config(),
                source_inspector=self.inspector,
                file_reader=self.reader,
            )
        self.assertEqual(0, self.inspector.calls)

        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_SOURCE_DIRTY",
        ):
            self.build(allow_dirty_staging_source=False)
        self.assertEqual(1, self.inspector.calls)
        self.assertEqual([], self.reader.calls)

    def test_mismatched_base_symlink_unsafe_mode_and_changed_source_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA_MISMATCH",
        ):
            self.build(expected_baseline_sha="a" * 40)

        target = inventory.REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS[0][1]
        self.reader.overrides[target] = inventory.PhysicalReleaseCandidateFileObservation(
            relative_path=target,
            owner_uid=0,
            mode=0o644,
            regular_file=True,
            symlink=True,
            stable=True,
            content=b"x",
        )
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_FILE_TYPE_INVALID",
        ):
            self.build()

        self.reader.overrides[target] = replace(
            self.reader.overrides[target], symlink=False, mode=0o664
        )
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_FILE_MODE_INVALID",
        ):
            self.build()

        self.reader.overrides.clear()
        changed = replace(self.inspector.value, stable=False)
        self.inspector.value = changed
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_SOURCE_UNSTABLE",
        ):
            self.build()

    def test_parser_rejects_selection_outside_allowlist_and_hash_tampering(self) -> None:
        result = self.build()
        value = __import__("json").loads(result.canonical_manifest)
        value["entries"][0]["path"] = "tmp/should-not-be-selected.py"
        forged = __import__("json").dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii") + b"\n"
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_MANIFEST_SELECTION_INVALID",
        ):
            inventory.parse_physical_release_candidate_inventory(forged)

        value = __import__("json").loads(result.canonical_manifest)
        value["entries"][0]["path"] = next(
            iter(inventory.RETIRED_PAIRED_ARVAN_S3_RUNTIME_PATHS)
        )
        forged = __import__("json").dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii") + b"\n"
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_RETIRED_PAIRED_RUNTIME_FORBIDDEN",
        ):
            inventory.parse_physical_release_candidate_inventory(forged)

        value = __import__("json").loads(result.canonical_manifest)
        value["entries"][0]["path"] = next(
            iter(inventory.RETIRED_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_PATHS)
        )
        forged = __import__("json").dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii") + b"\n"
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_V1_SINGLE_OBJECT_BASE_BACKUP_FORBIDDEN",
        ):
            inventory.parse_physical_release_candidate_inventory(forged)

        value = __import__("json").loads(result.canonical_manifest)
        value["entries"][0]["path"] = "../outside.py"
        forged = __import__("json").dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii") + b"\n"
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_MANIFEST_PATH_INVALID",
        ):
            inventory.parse_physical_release_candidate_inventory(forged)

        value = __import__("json").loads(result.canonical_manifest)
        value["entries"][0]["sha256"] = hashlib.sha256(b"changed").hexdigest()
        body = dict(value)
        del body["manifest_sha256"]
        value["manifest_sha256"] = hashlib.sha256(
            inventory._canonical_json(body)
        ).hexdigest()
        forged = inventory._canonical_json(value) + b"\n"
        parsed = inventory.parse_physical_release_candidate_inventory(forged)
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_INVENTORY_SOURCE_HASH_MISMATCH",
        ):
            with patch.object(inventory.os, "geteuid", return_value=0):
                inventory.verify_physical_release_candidate_inventory(
                    inventory=parsed,
                    config=self.config(),
                    source_inspector=self.inspector,
                    file_reader=self.reader,
                )

    def test_clean_target_baseline_check_has_no_materializer_and_refuses_dirty_target(self) -> None:
        with self.assertRaisesRegex(
            inventory.PhysicalReleaseCandidateInventoryError,
            "PHYSICAL_RELEASE_CANDIDATE_CLEAN_BASE_REQUIRES_STRICT_MODE",
        ):
            with patch.object(inventory.os, "geteuid", return_value=0):
                inventory.verify_clean_physical_release_candidate_base(
                    config=self.config(), source_inspector=self.inspector
                )

        self.inspector.value = replace(self.inspector.value, clean=True, stable=True)
        with patch.object(inventory.os, "geteuid", return_value=0):
            inventory.verify_clean_physical_release_candidate_base(
                config=self.config(allow_dirty_staging_source=False),
                source_inspector=self.inspector,
            )
