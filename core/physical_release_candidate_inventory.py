"""Fail-closed inventory for selectively materializing a physical release.

This module deliberately *does not* create a Git worktree, copy a file, stage
Git content, build an image, or contact a host.  It freezes only the reviewed
architecture paths which differ from the fixed clean baseline.  A later,
separate root-controlled materializer must first prove that its destination is
a clean checkout of that baseline, then transfer these exact bytes and obtain
a normal release seal.

The source being inventoried may be a dirty staging worktree only when that is
explicitly requested.  Such an inventory is evidence for selective review,
never a release, a backup of the whole worktree, or authority to deploy.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Protocol


__all__ = (
    "FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA",
    "FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE",
    "PHYSICAL_RELEASE_CANDIDATE_INVENTORY_DEFAULT_ENABLED",
    "PHYSICAL_RELEASE_CANDIDATE_INVENTORY_SCHEMA",
    "PhysicalReleaseCandidateFileObservation",
    "PhysicalReleaseCandidateFileReader",
    "PhysicalReleaseCandidateInventoryConfig",
    "PhysicalReleaseCandidateInventoryError",
    "PhysicalReleaseCandidateSourceInspection",
    "PhysicalReleaseCandidateSourceInspector",
    "PhysicalReleaseCandidateSourceObject",
    "PhysicalReleaseCandidateInventory",
    "PhysicalReleaseCandidateInventoryEntry",
    "ACTIVE_OPERATIONAL_V1_PHYSICAL_RELEASE_CANDIDATE_PATHS",
    "ACTIVE_V2_PHYSICAL_RELEASE_CANDIDATE_PATHS",
    "ACTIVE_V4_PHYSICAL_RELEASE_CANDIDATE_PATHS",
    "V4_WITNESS_EXECUTION_LEGACY_FORBIDDEN_PATHS",
    "RETIRED_PAIRED_ARVAN_S3_RUNTIME_PATHS",
    "RETIRED_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_PATHS",
    "REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS",
    "V1_SINGLE_OBJECT_BASE_BACKUP_COMPATIBILITY_ONLY_PATHS",
    "build_physical_release_candidate_inventory",
    "parse_physical_release_candidate_inventory",
    "verify_clean_physical_release_candidate_base",
    "verify_physical_release_candidate_inventory",
)


PHYSICAL_RELEASE_CANDIDATE_INVENTORY_SCHEMA = (
    "gold-trade-physical-release-candidate-inventory-v1"
)
PHYSICAL_RELEASE_CANDIDATE_INVENTORY_DEFAULT_ENABLED = False

# These are Git object identities, not hosts, credentials, artifacts, or a
# statement that the current dirty worktree is releasable.
FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA = (
    "6091a020b9c66753af135e3a4dcaa919e6bd049d"
)
FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE = (
    "bc91aee560d34e6f77dcbce0da287c38d8a1b95a"
)

_HEX40_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9._/-]{0,511}$", re.ASCII)
_GROUP_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$", re.ASCII)
_ALLOWED_MODES = frozenset({0o644, 0o755})
_MAX_ENTRY_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_BYTES = 128 * 1024 * 1024

# The former paired credential/client surface is retained in the source tree
# only for historical compatibility and must never re-enter a candidate that
# can progress toward a three-site Full-Matrix campaign.  This is deliberately
# a literal deny-list in addition to the literal reviewed allow-list: a future
# list edit or forged manifest cannot silently revive a paired runtime.
RETIRED_PAIRED_ARVAN_S3_RUNTIME_PATHS = frozenset(
    {
        "core/physical_arvan_s3_failback_separated_client_factory.py",
        "core/physical_arvan_s3_failback_separated_credential_loader.py",
        "core/physical_arvan_s3_immutability_probe_runner.py",
        "core/physical_arvan_s3_separated_client_factory.py",
        "core/physical_arvan_s3_separated_credential_loader.py",
    }
)

# These modules form the historical single-ciphertext-object base-backup
# activation chain.  They are retained in the source worktree for forensic
# reading and migration, but the candidate used for the physical Full Matrix
# must not materialize any of their capture, handoff, pull, or materialization
# entry points.  The chunked v2 publisher/receiver path is intentionally not
# added here until its complete activation contract is reviewed.
RETIRED_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_PATHS = frozenset(
    {
        "core/physical_arvan_s3_fi_receiver_failback_role_factory.py",
        "core/physical_arvan_s3_ir_publisher_failback_role_factory.py",
        "core/physical_postgres_standby_bootstrap_materialization.py",
        "core/physical_wa_fi_postgres_base_backup_capture_command.py",
        "core/physical_wa_fi_postgres_failback_materialization_runtime.py",
        "core/physical_wa_fi_postgres_failback_pull_runtime.py",
        "core/physical_wa_fi_postgres_helper_capture_bridge.py",
        "core/physical_wa_fi_postgres_object_storage_handoff_runtime.py",
        "core/physical_wa_ir_postgres_failback_capture_bridge.py",
        "core/physical_wa_ir_postgres_failback_handoff_runtime.py",
        "core/physical_wa_ir_postgres_recovery_materialization_runtime.py",
        "core/physical_wa_ir_postgres_recovery_pull_runtime.py",
    }
)

# The verified V1 lineage/data-shape readers remain deliberately available to
# the v2 bridge, but are not activation entry points.  Keeping this set
# explicit prevents a future review from treating their presence as a license
# to re-add a V1 capture/uploader runtime to the candidate.
V1_SINGLE_OBJECT_BASE_BACKUP_COMPATIBILITY_ONLY_PATHS = frozenset(
    {
        "core/physical_wal_base_backup_spool.py",
        "core/physical_wal_incremental_receiver_chain.py",
        "core/physical_wal_object_manifest.py",
        "core/physical_wal_receiver_staging.py",
    }
)

# Operational V1 is the separately reviewed, Witness-fenced one-writer
# foundation.  Keep this literal so an installer/release review cannot omit a
# durable local component while still treating the group as live deployment.
# These modules remain default-off and do not grant a promotion or writer by
# their inclusion in a candidate inventory.
ACTIVE_OPERATIONAL_V1_PHYSICAL_RELEASE_CANDIDATE_PATHS = frozenset(
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

# These are the complete, reviewed v2 base-backup and four-role Object
# Storage foundations that the active three-site path may import.  Keep this
# literal and narrow: it is an integrity invariant for the candidate, not a
# discovery mechanism.  In particular, a future v2 remote-ack bridge is not
# selected merely because its filename resembles these modules; it must be
# added here and to the literal reviewed group in the same reviewed change
# after its implementation and tests are final.
ACTIVE_V2_PHYSICAL_RELEASE_CANDIDATE_PATHS = frozenset(
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

# V4 is a separately reviewed Witness-mediated execution generation.  These
# files must remain a literal set: an unreviewed future V4-looking module may
# not become release material merely by sharing a prefix.  The set intentionally
# contains only the settled pure/append-only boundary; live transport adapters
# are added only after their own focused review and tests are complete.
ACTIVE_V4_PHYSICAL_RELEASE_CANDIDATE_PATHS = frozenset(
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

# Some historical Full-Matrix control modules are intentionally still selected
# for containment/fencing and forensic compatibility.  That does not make
# them valid dependencies of the isolated V4 Witness execution boundary.  A
# direct import of any one of these paths is a release-time architecture
# regression even if the historical path remains otherwise reviewed.
V4_WITNESS_EXECUTION_LEGACY_FORBIDDEN_PATHS = frozenset(
    {
        "core/legacy_two_server_full_matrix_fence.py",
        "core/physical_full_matrix_campaign_readiness.py",
        "core/physical_full_matrix_execution_driver.py",
        "core/physical_full_matrix_execution_driver_v3.py",
        "core/physical_full_matrix_receipt_journal.py",
    }
)


class PhysicalReleaseCandidateInventoryError(ValueError):
    """A stable refusal from the selective release-candidate inventory."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalReleaseCandidateInventoryError(code)


def _paths(value: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in value.splitlines() if line.strip())


# The lists are intentionally literal rather than globs.  A newly appearing
# ``physical_*.py`` or a file underneath an otherwise reviewed directory is
# *not* silently selected.  Tests and documentation are deliberately outside
# the runtime transfer set; they are reviewed and committed with the release
# separately, not swept in from a dirty worktree by this tool.
_REVIEWED_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "writer-fencing-runtime",
        _paths(
            """
.gitignore
api/deps.py
api/routers/auth.py
api/routers/chat.py
api/routers/realtime.py
api/routers/sync.py
bot/middlewares/__init__.py
bot/middlewares/writer_term.py
bot/repeat_offer.py
bot/utils/trade_suggestion_messages.py
bot/writer_readiness.py
core/application_snapshot_write_barrier.py
core/application_writer_term.py
core/application_writer_transaction_envelope_guard.py
core/config.py
core/connectivity.py
core/db.py
core/events.py
core/external_effect_execution_gate.py
core/fenced_fi_release_identity.py
core/notifications.py
core/offer_publication_worker.py
core/otp_sms_fallback_worker.py
core/physical_operational_failover_v1.py
core/physical_operational_failover_v1_witness_ledger.py
core/physical_operational_failover_v1_witness_ledger_durable_cas.py
core/physical_operational_failover_v1_witness_term_issuer.py
core/physical_operational_failover_v1_witness_term_replay_guard.py
core/physical_operational_failover_v1_witness_term_revalidator.py
core/physical_operational_failover_v1_v2_writer_term_bridge.py
core/physical_operational_failover_v1_v2_writer_term_bridge_runtime_issuer.py
core/physical_operational_failover_v1_writer_admission.py
core/physical_operational_failover_v1_writer_admission_durable_state.py
core/physical_operational_failover_v1_writer_admission_postgres_contract.py
core/physical_operational_failover_v1_writer_admission_sqlalchemy_transaction.py
core/physical_operational_failover_v1_writer_transaction_envelope.py
core/production_writer_lease.py
core/security.py
core/server_routing.py
core/services/invitation_sms_delivery_service.py
core/services/otp_sms_delivery_service.py
core/services/telegram_otp_delivery_service.py
core/sms.py
core/telegram_admin_broadcast_worker.py
core/telegram_notification_outbox_worker.py
core/trade_delivery_worker.py
core/utils.py
core/web_push.py
main.py
migrations/versions/0writeradm01_add_operational_writer_admission_schema.py
models/operational_writer_admission.py
run_bot.py
"""
        ),
    ),
    (
        "object-delta-data-plane",
        _paths(
            """
core/append_only_sync_delta_batch.py
core/append_only_sync_delta_payload.py
core/authorized_object_delta_receiver_transaction.py
core/dedicated_object_delta_atomic_applier.py
core/legacy_source_publication_fence.py
core/object_delta_baseline_manifest.py
core/object_delta_batch_assembler.py
core/object_delta_delivery_control_packet.py
core/object_delta_import_plan.py
core/object_delta_mvp_canonical.py
core/object_delta_mvp_full_mirror_fence.py
core/object_delta_mvp_scope.py
core/object_delta_outbox_allocator.py
core/object_delta_outbox_runtime.py
core/object_delta_receiver_apply_scope.py
core/object_delta_receiver_delivery_binding.py
core/object_delta_receiver_delivery_nonce.py
core/object_delta_receiver_delivery_nonce_persistence.py
core/object_delta_receiver_genesis_admission.py
core/object_delta_receiver_mvp_handlers.py
core/object_delta_receiver_payload_admission.py
core/object_delta_receiver_registry.py
core/object_delta_role_matrix.py
core/object_delta_role_matrix_rollover.py
core/object_delta_runtime_binding.py
core/object_delta_source_batch_attestation.py
core/object_delta_source_batch_ledger.py
core/object_delta_source_batch_publication.py
core/object_delta_source_batch_selection.py
core/object_delta_source_cutover_attestation.py
core/object_delta_source_cutover_publication_gate.py
core/object_delta_source_ledger_persistence.py
core/object_delta_source_preupload_authorization.py
core/object_delta_source_publication_attempt.py
core/object_delta_source_publication_attempt_persistence.py
core/object_delta_source_publication_snapshot.py
core/object_delta_source_transport_contract.py
core/object_delta_transport_binding.py
core/sqlalchemy_authorized_object_delta_receiver_transaction.py
core/sync_outbox_guard.py
core/sync_push.py
core/sync_worker.py
migrations/env.py
migrations/versions/a1b2c3d4e5f6_add_object_delta_schema.py
migrations/versions/b2c3d4e5f6a7_add_object_delta_source_batch_ledger.py
migrations/versions/c3d4e5f6a7b8_add_object_delta_receiver_delivery_nonce_receipts.py
migrations/versions/d4e5f6a7b8c9_add_object_delta_source_cutover.py
migrations/versions/e5f6a7b8c9d0_add_object_delta_nonce_import_binding.py
migrations/versions/f6a7b8c9d0e2_add_object_delta_source_append_only_guards.py
migrations/versions/g7a8b9c0d1e2_add_object_delta_source_publication_attempts.py
migrations/versions/h8i9j0k1l2m3_add_promotion_auth_epoch.py
migrations/versions/i9j0k1l2m3n4_add_promotion_auth_epoch_operations.py
models/__init__.py
models/object_delta.py
models/object_delta_receiver_delivery.py
models/object_delta_source_batch.py
models/object_delta_source_publication_attempt.py
models/promotion_auth_epoch.py
"""
        ),
    ),
    (
        "object-storage-and-wal",
        _paths(
            """
core/physical_age_v1_adapter.py
core/physical_arvan_exact_version_pull.py
core/physical_arvan_immutability_preflight.py
core/physical_arvan_s3_client_factory.py
core/physical_arvan_s3_failback_route_commitment.py
core/physical_arvan_s3_fi_publisher_role_factory.py
core/physical_arvan_s3_four_role_immutability_live_probe_runtime.py
core/physical_arvan_s3_four_role_immutability_role_local_collector.py
core/physical_arvan_s3_four_role_immutability_witness_dispatch_ledger.py
core/physical_arvan_s3_four_role_immutability_witness_orchestration.py
core/physical_arvan_s3_four_role_immutability_witness_role_agent.py
core/physical_arvan_s3_four_role_immutability_preflight.py
core/physical_arvan_s3_four_role_live_iam_durable_admission_bridge.py
core/physical_arvan_s3_four_role_live_iam_evidence.py
core/physical_arvan_s3_four_role_live_iam_preflight_gate.py
core/physical_arvan_s3_four_role_live_iam_witness_ledger_runtime.py
core/physical_arvan_s3_four_role_preflight_binding.py
core/physical_full_matrix_v2_gen2_witnessed_ack_chain.py
core/physical_full_matrix_v2_gen2_witnessed_campaign_readiness.py
core/physical_full_matrix_v2_recovery_evidence.py
core/physical_full_matrix_v2_witnessed_ack_chain.py
core/physical_full_matrix_v2_witnessed_campaign_readiness.py
core/physical_postgres_chunked_base_backup_recovery_preflight.py
core/physical_postgres_chunked_base_backup_recovery_readback_attestation.py
core/physical_postgres_chunked_base_backup_target_recovery_preflight.py
core/physical_arvan_s3_immutability_live_probe.py
core/physical_arvan_s3_ir_receiver_role_loader.py
core/physical_arvan_s3_role_local_client_support.py
core/physical_arvan_s3_role_local_credential_reader.py
core/physical_arvan_s3_role_local_identity.py
core/physical_arvan_s3_role_local_route_policy.py
core/physical_arvan_s3_role_profiles.py
core/physical_blob_artifact_spool.py
core/physical_blob_object_storage_uploader.py
core/physical_blob_pre_cas_acceptance.py
core/physical_blob_receiver_exact_pull_staging.py
core/physical_blob_receiver_inventory_mapping.py
core/physical_blob_receiver_promotion_evidence.py
core/physical_durable_replay_attestation_ledger.py
core/physical_strict_remote_ack_writer_response.py
core/physical_ir_to_fi_object_storage_failback_preflight.py
core/physical_wa_ir_bootstrap_bundle_builder.py
core/physical_wal_archive_spool.py
core/physical_wal_base_backup_spool.py
core/physical_wal_chunked_base_backup_handoff_receipt.py
core/physical_wal_chunked_base_backup_lineage_envelope.py
core/physical_wal_chunked_base_backup_blob_frontier_coverage.py
core/physical_wal_chunked_base_backup_manifest.py
core/physical_wal_chunked_base_backup_publisher_runtime.py
core/physical_wal_chunked_base_backup_receiver_receipt_ledger.py
core/physical_wal_chunked_base_backup_receiver_staging_runtime.py
core/physical_wal_chunked_base_backup_recovery_admission.py
core/physical_wal_chunked_base_backup_remote_ack_bridge.py
core/physical_wal_chunked_base_backup_resume_admission.py
core/physical_wal_chunked_base_backup_target_wal_continuity.py
core/physical_wal_chunked_base_backup_transfer.py
core/physical_wal_v2_remote_ack.py
core/physical_wal_v2_remote_ack_coverage.py
core/physical_wal_v2_remote_ack_receiver_ledger.py
core/physical_wal_v2_strict_remote_ack_writer_response.py
core/physical_wal_v2_witness_roundtrip_contract.py
core/physical_wal_v2_witness_roundtrip_delivery_contract.py
core/physical_wal_v2_witness_roundtrip_delivery_runtime.py
core/physical_wal_v2_witness_roundtrip_arvan_s3v4_delivery_dispatcher.py
core/physical_wal_v2_witness_roundtrip_full_bundle_issuer.py
core/physical_wal_v2_witness_roundtrip_full_bundle_deployment_reference.py
core/physical_wal_v2_witness_roundtrip_arvan_s3v4_scope.py
core/physical_wal_v2_witness_roundtrip_deployment_plan.py
core/physical_wal_v2_witness_roundtrip_mailbox_admission.py
core/physical_wal_v2_witness_roundtrip_s3_mailbox_adapter.py
core/physical_wal_v2_witness_roundtrip_source_outbox.py
core/physical_wal_v2_witness_roundtrip_strict_writer_bound_response.py
core/physical_wal_v2_witness_roundtrip_strict_writer_bound_sqlalchemy_transaction.py
core/physical_wal_v2_witness_roundtrip_strict_writer_bound_transaction_envelope.py
core/physical_wal_v2_witness_roundtrip_strict_writer_response.py
core/physical_wal_v2_witness_roundtrip_witness_ledger.py
core/physical_wal_incremental_receiver_chain.py
core/physical_wal_manifest_object_storage_transport.py
core/physical_wal_object_manifest.py
core/physical_wal_object_storage_uploader.py
core/physical_wal_promotion_gate.py
core/physical_wal_receiver_staging.py
core/physical_wal_remote_ack.py
core/physical_wal_remote_ack_object_storage_transport.py
core/physical_wal_remote_ack_receiver_ledger.py
core/physical_wal_remote_ack_witness_locator_ledger.py
core/physical_wal_source_manifest_assembler.py
migrations/versions/0v2strictdb01_add_v2_witness_strict_writer_schema.py
migrations/versions/0v2strictbind01_add_v2_witness_bound_writer_schema.py
migrations/versions/0v2consreg01_add_v2_witness_attestation_consumption_registry.py
migrations/versions/0v2basepin01_add_v2_gen2_base_pin_columns.py
models/physical_wal_v2_witness_roundtrip_strict_writer.py
models/physical_wal_v2_witness_roundtrip_strict_writer_bound.py
models/physical_wal_v2_witness_roundtrip_attestation_consumption.py
"""
        ),
    ),
    (
        "physical-postgres-and-promotion",
        _paths(
            """
core/physical_postgres_data_plane_preflight.py
core/physical_postgres_deployment_scaffold.py
core/physical_postgres_promotion_coordinator.py
core/promotion_p0_continuity_preflight.py
core/physical_postgres_recovery_preflight.py
core/physical_postgres_recovery_readback_collector.py
core/physical_postgres_strict_runtime_installation_gate.py
core/physical_wa_fi_postgres_archive_command.py
core/physical_wa_fi_postgres_helper_container.py
core/services/promotion_continuity_participants.py
core/services/promotion_session_invalidation_service.py
core/services/promotion_upload_cleanup_service.py
deploy/physical-postgres/docker-compose.primary.yml.template
deploy/physical-postgres/docker-compose.standby.yml.template
deploy/physical-postgres/primary-pg_hba.conf.template
deploy/physical-postgres/primary-pg_ident.conf.template
deploy/physical-postgres/primary-postgresql.conf.template
deploy/physical-postgres/standby-pg_hba.conf.template
deploy/physical-postgres/standby-postgresql.conf.template
scripts/guard_physical_postgres_launch.py
scripts/render_physical_postgres_deployment.py
"""
        ),
    ),
    (
        "preflight-and-release-controls",
        _paths(
            """
core/dedicated_host_preflight_aggregate.py
core/dedicated_host_preflight_arvan_ecc_readback.py
core/dedicated_host_preflight_controller.py
core/dedicated_host_preflight_fi_request_provisioning_runtime.py
core/dedicated_host_preflight_ir_object_storage_pull_delivery.py
core/dedicated_host_preflight_ir_object_storage_runtime.py
core/dedicated_host_preflight_ir_request_provisioning.py
core/dedicated_host_preflight_ir_request_provisioning_runtime.py
core/dedicated_host_preflight_ir_witness_attestation.py
core/dedicated_host_preflight_ir_witness_attestation_runtime.py
core/dedicated_host_preflight_pinned_ssh_delivery.py
core/dedicated_host_preflight_receipt.py
core/dedicated_host_preflight_receipt_agent_boundary.py
core/dedicated_host_preflight_receipt_agent_installation.py
core/dedicated_host_preflight_runtime_transport.py
core/dedicated_host_preflight_witness_attestation_ledger.py
core/dedicated_host_preflight_witness_attestation_runtime.py
core/dedicated_host_preflight_witness_evidence_pinned_ssh_delivery.py
core/fenced_fi_release_identity_runtime_binding.py
core/legacy_two_server_full_matrix_fence.py
core/physical_full_matrix_campaign_readiness.py
core/physical_full_matrix_execution_driver.py
core/physical_full_matrix_receipt_journal.py
core/physical_release_candidate_inventory.py
core/physical_release_candidate_writer_quiescence_receipt.py
core/physical_release_seal_admission.py
core/physical_release_seal_local_inspection_adapter.py
core/webapp_ir_dark_snapshot_preflight.py
deploy/production/docker-compose.webapp-fi-writer-2c08.yml
deploy/production/docker-compose.webapp-ir-promoted-2c08.yml
deploy/production/production-writer-lease-agent.webapp-fi-fenced-2c08.json.example
deploy/production/webapp-fi-fenced-writer-2c08.env.example
deploy/production/webapp-fi-writer-lease-guard-preflight.json.example
deploy/systemd/trading-bot-production-writer-fi-fenced-lease-guard.service.template
scripts/assess_physical_full_matrix_campaign_readiness.py
scripts/dedicated_host_preflight_manifest.py
scripts/preflight_fenced_fi_writer.py
scripts/preflight_webapp_ir_dark_snapshot_standby.py
scripts/render_fenced_fi_writer_lease_guard_unit.py
scripts/render_dedicated_host_preflight_receipt_agent.py
scripts/install_dedicated_host_preflight_receipt_agent.py
scripts/run_dedicated_host_preflight_receipt_dispatcher.py
scripts/run_dedicated_host_preflight_root_collector.py
scripts/run_dedicated_host_preflight_witness_evidence_dispatcher.py
scripts/run_dedicated_host_preflight_witness_evidence_root_collector.py
scripts/run_dedicated_host_readonly_preflight.py
scripts/run_dedicated_host_readonly_preflight_controller.py
scripts/verify_dedicated_host_readonly_preflight.py
scripts/run_production_full_matrix.py
scripts/run_staging_two_server_full_matrix.py
scripts/verify_fenced_fi_release_identity.py
"""
        ),
    ),
    (
        "v4-witness-execution-boundary",
        _paths(
            """
core/physical_full_matrix_execution_driver_v4.py
core/physical_full_matrix_v4_final_convergence_admission.py
core/physical_full_matrix_v4_fi_fence_scope_installation_provenance.py
core/physical_full_matrix_v4_materialization_preflight.py
core/physical_full_matrix_v4_phase1_post_effect_strict_ack_boundary.py
core/physical_full_matrix_v4_phase1_strict_ack_provenance.py
core/physical_full_matrix_v4_phase3_recovery_admission.py
core/physical_full_matrix_v4_phase6_fd_only_rebuild_binder.py
core/physical_full_matrix_v4_phase6_failback_rebuild_admission.py
core/physical_full_matrix_v4_phase_installation_provenance.py
core/physical_full_matrix_v4_root_composition.py
core/physical_full_matrix_v4_plan_rehydration.py
core/physical_full_matrix_v4_receipt_journal.py
core/physical_full_matrix_v4_retired_fi_predecessor_fence.py
core/physical_full_matrix_v4_retired_fi_predecessor_fence_runtime.py
core/physical_full_matrix_v4_witness_anchor_adapter.py
core/physical_full_matrix_v4_witness_anchor_fi_witness_anti_replay_registry.py
core/physical_full_matrix_v4_witness_anchor_fi_witness_mailbox.py
core/physical_full_matrix_v4_witness_anchor_ledger.py
core/physical_full_matrix_v4_witness_anchor_wire.py
core/physical_full_matrix_v4_witness_successor_transition_evidence.py
core/physical_full_matrix_v4_witness_successor_transition_runtime.py
"""
        ),
    ),
    (
        "v4-phase6-reverse-bundle-descriptor-binding-foundation",
        _paths(
            """
core/physical_full_matrix_v4_phase6_reverse_bundle_descriptor_binding.py
core/physical_full_matrix_v4_phase6_source_fd_attestation.py
core/physical_full_matrix_v4_phase6_reconstruction_handoff.py
"""
        ),
    ),
    (
        "source-stage-integration",
        _paths(
            """
deploy.sh
scripts/adapt_exact_release_frontend_static_build.py
scripts/install_webapp_fi_source_adoption.py
scripts/manage_webapp_fi_source_transport.py
scripts/manage_webapp_ir_artifact_stage.py
scripts/manage_webapp_ir_release_provenance.py
scripts/manage_webapp_ir_snapshot.py
scripts/prepare_exact_release_frontend_static_build.py
scripts/prepare_webapp_fi_source_adoption.py
scripts/prepare_webapp_fi_static_assets.py
scripts/prepare_webapp_ir_stage_bootstrap.py
scripts/production_deploy_online.sh
scripts/production_writer_lease_agent.py
scripts/receive_webapp_fi_source_evidence.py
scripts/receive_webapp_fi_source_object.py
scripts/recover_cross_server_sync.sh
scripts/render_webapp_fi_initial_static_upload.py
scripts/render_webapp_fi_post_packet_upload.py
scripts/render_webapp_fi_source_bootstrap_receive.py
scripts/render_webapp_fi_static_prepare.py
scripts/render_webapp_fi_static_provenance_receive.py
scripts/render_webapp_ir_stage_bootstrap_receive.py
scripts/render_webapp_ir_stage_consume.py
scripts/render_webapp_ir_static_receive.py
scripts/run_webapp_ir_seven_object_stage.py
scripts/seed_shared_sync_tables.py
scripts/sync_repair_tool.py
scripts/webapp_fi_source_transport_contract.py
scripts/webapp_fi_static_provenance_control_packet.py
"""
        ),
    ),
)


def _flatten_reviewed_paths() -> tuple[tuple[str, str], ...]:
    seen: set[str] = set()
    selected: list[tuple[str, str]] = []
    for group, paths in _REVIEWED_GROUPS:
        if _GROUP_RE.fullmatch(group) is None:
            raise RuntimeError("invalid reviewed candidate group")
        for relative_path in paths:
            _require_safe_relative_path(relative_path, code="INVALID_REVIEWED_PATH")
            if relative_path in RETIRED_PAIRED_ARVAN_S3_RUNTIME_PATHS:
                raise RuntimeError("retired paired runtime selected for candidate inventory")
            if relative_path in RETIRED_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_PATHS:
                raise RuntimeError("retired v1 base-backup activation selected for candidate inventory")
            if relative_path in seen:
                raise RuntimeError("duplicate reviewed candidate path")
            seen.add(relative_path)
            selected.append((group, relative_path))
    selected_groups = {relative_path: group for group, relative_path in selected}
    if ACTIVE_V2_PHYSICAL_RELEASE_CANDIDATE_PATHS & (
        RETIRED_PAIRED_ARVAN_S3_RUNTIME_PATHS
        | RETIRED_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_PATHS
    ):
        raise RuntimeError("active v2 path overlaps retired runtime inventory")
    if not ACTIVE_V2_PHYSICAL_RELEASE_CANDIDATE_PATHS <= set(selected_groups):
        raise RuntimeError("active v2 runtime omitted from candidate inventory")
    if any(
        selected_groups[path] != "object-storage-and-wal"
        for path in ACTIVE_V2_PHYSICAL_RELEASE_CANDIDATE_PATHS
    ):
        raise RuntimeError("active v2 runtime selected outside object-storage-and-wal")
    if not ACTIVE_V4_PHYSICAL_RELEASE_CANDIDATE_PATHS <= set(selected_groups):
        raise RuntimeError("active v4 runtime omitted from candidate inventory")
    if ACTIVE_V4_PHYSICAL_RELEASE_CANDIDATE_PATHS & V4_WITNESS_EXECUTION_LEGACY_FORBIDDEN_PATHS:
        raise RuntimeError("active v4 runtime overlaps forbidden legacy execution inventory")
    if any(
        selected_groups[path] != "v4-witness-execution-boundary"
        for path in ACTIVE_V4_PHYSICAL_RELEASE_CANDIDATE_PATHS
    ):
        raise RuntimeError("active v4 runtime selected outside v4-witness-execution-boundary")
    if not ACTIVE_OPERATIONAL_V1_PHYSICAL_RELEASE_CANDIDATE_PATHS <= set(selected_groups):
        raise RuntimeError("active operational v1 runtime omitted from candidate inventory")
    if any(
        selected_groups[path] != "writer-fencing-runtime"
        for path in ACTIVE_OPERATIONAL_V1_PHYSICAL_RELEASE_CANDIDATE_PATHS
    ):
        raise RuntimeError("active operational v1 runtime selected outside writer-fencing-runtime")
    return tuple(selected)


def _require_safe_relative_path(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _SAFE_PATH_RE.fullmatch(value) is None:
        _fail(code)
    if value.startswith("/") or "//" in value:
        _fail(code)
    components = value.split("/")
    if any(component in {"", ".", ".."} for component in components):
        _fail(code)
    return value


REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS = _flatten_reviewed_paths()
_REVIEWED_GROUP_BY_PATH = {
    relative_path: group
    for group, relative_path in REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS
}


@dataclass(frozen=True)
class PhysicalReleaseCandidateSourceObject:
    path: Path
    owner_uid: int
    mode: int
    directory: bool
    symlink: bool
    ancestors_root_controlled: bool


@dataclass(frozen=True)
class PhysicalReleaseCandidateSourceInspection:
    source_root: PhysicalReleaseCandidateSourceObject
    release_sha: str
    git_tree_id: str
    clean: bool
    stable: bool


class PhysicalReleaseCandidateSourceInspector(Protocol):
    """Read-only source/Git metadata observation supplied by a future adapter."""

    def inspect_source(
        self, *, source_root: Path
    ) -> PhysicalReleaseCandidateSourceInspection:
        """Return source-root and exact local Git identity facts without mutation."""


@dataclass(frozen=True)
class PhysicalReleaseCandidateFileObservation:
    relative_path: str
    owner_uid: int
    mode: int
    regular_file: bool
    symlink: bool
    stable: bool
    content: bytes


class PhysicalReleaseCandidateFileReader(Protocol):
    """Bounded no-follow file reader supplied by a future local adapter."""

    def read_file(
        self, *, source_root: Path, relative_path: str
    ) -> PhysicalReleaseCandidateFileObservation:
        """Read precisely one reviewed source file without copying or writing it."""


@dataclass(frozen=True)
class PhysicalReleaseCandidateInventoryConfig:
    source_root: Path | None = None
    expected_baseline_sha: str = ""
    expected_baseline_tree: str = ""
    enabled: bool = PHYSICAL_RELEASE_CANDIDATE_INVENTORY_DEFAULT_ENABLED
    allow_dirty_staging_source: bool = False


@dataclass(frozen=True)
class PhysicalReleaseCandidateInventoryEntry:
    group: str
    relative_path: str
    file_type: str
    mode: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class PhysicalReleaseCandidateInventory:
    canonical_manifest: bytes
    manifest_sha256: str
    baseline_release_sha: str
    baseline_git_tree_id: str
    source_dirty_at_capture: bool
    entries: tuple[PhysicalReleaseCandidateInventoryEntry, ...]
    materialization_authorized: bool = False
    release_authorized: bool = False
    execution_authorized: bool = False


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise PhysicalReleaseCandidateInventoryError("CANONICAL_JSON_INVALID") from exc


def _validate_config(config: PhysicalReleaseCandidateInventoryConfig) -> Path:
    if config.enabled is not True:
        _fail("PHYSICAL_RELEASE_CANDIDATE_INVENTORY_DISABLED")
    if os.geteuid() != 0:
        _fail("PHYSICAL_RELEASE_CANDIDATE_INVENTORY_ROOT_RUNTIME_REQUIRED")
    if not isinstance(config.source_root, Path) or not config.source_root.is_absolute():
        _fail("PHYSICAL_RELEASE_CANDIDATE_SOURCE_ROOT_INVALID")
    if config.expected_baseline_sha != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA:
        _fail("PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA_MISMATCH")
    if config.expected_baseline_tree != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE:
        _fail("PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE_MISMATCH")
    return config.source_root


def _validate_source_inspection(
    inspection: PhysicalReleaseCandidateSourceInspection,
    *,
    source_root: Path,
    require_clean: bool,
) -> None:
    source = inspection.source_root
    if source.path != source_root or not source.directory or source.symlink:
        _fail("PHYSICAL_RELEASE_CANDIDATE_SOURCE_OBJECT_INVALID")
    if source.owner_uid != 0 or not source.ancestors_root_controlled:
        _fail("PHYSICAL_RELEASE_CANDIDATE_SOURCE_OWNERSHIP_INVALID")
    if source.mode & 0o022:
        _fail("PHYSICAL_RELEASE_CANDIDATE_SOURCE_MODE_UNSAFE")
    if inspection.release_sha != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA:
        _fail("PHYSICAL_RELEASE_CANDIDATE_SOURCE_RELEASE_MISMATCH")
    if inspection.git_tree_id != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE:
        _fail("PHYSICAL_RELEASE_CANDIDATE_SOURCE_TREE_MISMATCH")
    if inspection.stable is not True:
        _fail("PHYSICAL_RELEASE_CANDIDATE_SOURCE_UNSTABLE")
    if require_clean and inspection.clean is not True:
        _fail("PHYSICAL_RELEASE_CANDIDATE_SOURCE_DIRTY")


def _entry_from_observation(
    *, group: str, relative_path: str, observation: PhysicalReleaseCandidateFileObservation
) -> PhysicalReleaseCandidateInventoryEntry:
    _require_safe_relative_path(observation.relative_path, code="PHYSICAL_RELEASE_CANDIDATE_PATH_INVALID")
    if observation.relative_path != relative_path:
        _fail("PHYSICAL_RELEASE_CANDIDATE_PATH_MISMATCH")
    if observation.symlink or not observation.regular_file:
        _fail("PHYSICAL_RELEASE_CANDIDATE_FILE_TYPE_INVALID")
    if observation.owner_uid != 0:
        _fail("PHYSICAL_RELEASE_CANDIDATE_FILE_OWNERSHIP_INVALID")
    if observation.mode not in _ALLOWED_MODES:
        _fail("PHYSICAL_RELEASE_CANDIDATE_FILE_MODE_INVALID")
    if observation.stable is not True:
        _fail("PHYSICAL_RELEASE_CANDIDATE_FILE_UNSTABLE")
    if not isinstance(observation.content, bytes) or len(observation.content) > _MAX_ENTRY_BYTES:
        _fail("PHYSICAL_RELEASE_CANDIDATE_FILE_SIZE_INVALID")
    return PhysicalReleaseCandidateInventoryEntry(
        group=group,
        relative_path=relative_path,
        file_type="regular",
        mode=f"{observation.mode:06o}",
        size_bytes=len(observation.content),
        sha256=hashlib.sha256(observation.content).hexdigest(),
    )


def _manifest_fields(
    *, source_dirty_at_capture: bool, entries: tuple[PhysicalReleaseCandidateInventoryEntry, ...]
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_RELEASE_CANDIDATE_INVENTORY_SCHEMA,
        "status": "draft-unsealed-staging-inventory-not-materialized",
        "baseline_release_sha": FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA,
        "baseline_git_tree_id": FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE,
        "source_dirty_at_capture": source_dirty_at_capture,
        "entries": [
            {
                "group": entry.group,
                "path": entry.relative_path,
                "type": entry.file_type,
                "mode": entry.mode,
                "size_bytes": entry.size_bytes,
                "sha256": entry.sha256,
            }
            for entry in entries
        ],
        "materialization_authorized": False,
        "release_authorized": False,
        "execution_authorized": False,
    }


def build_physical_release_candidate_inventory(
    *,
    config: PhysicalReleaseCandidateInventoryConfig,
    source_inspector: PhysicalReleaseCandidateSourceInspector,
    file_reader: PhysicalReleaseCandidateFileReader,
) -> PhysicalReleaseCandidateInventory:
    """Freeze reviewed files from a root-controlled local source, without copying them."""

    source_root = _validate_config(config)
    before = source_inspector.inspect_source(source_root=source_root)
    _validate_source_inspection(
        before,
        source_root=source_root,
        require_clean=not config.allow_dirty_staging_source,
    )
    entries = tuple(
        _entry_from_observation(
            group=group,
            relative_path=relative_path,
            observation=file_reader.read_file(
                source_root=source_root, relative_path=relative_path
            ),
        )
        for group, relative_path in REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS
    )
    if sum(entry.size_bytes for entry in entries) > _MAX_TOTAL_BYTES:
        _fail("PHYSICAL_RELEASE_CANDIDATE_TOTAL_SIZE_INVALID")
    after = source_inspector.inspect_source(source_root=source_root)
    _validate_source_inspection(
        after,
        source_root=source_root,
        require_clean=not config.allow_dirty_staging_source,
    )
    if before != after:
        _fail("PHYSICAL_RELEASE_CANDIDATE_SOURCE_CHANGED_DURING_CAPTURE")
    body = _manifest_fields(source_dirty_at_capture=not before.clean, entries=entries)
    manifest_sha256 = hashlib.sha256(_canonical_json(body)).hexdigest()
    value = dict(body)
    value["manifest_sha256"] = manifest_sha256
    canonical_manifest = _canonical_json(value) + b"\n"
    return PhysicalReleaseCandidateInventory(
        canonical_manifest=canonical_manifest,
        manifest_sha256=manifest_sha256,
        baseline_release_sha=FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA,
        baseline_git_tree_id=FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE,
        source_dirty_at_capture=not before.clean,
        entries=entries,
    )


def _parse_entry(value: object) -> PhysicalReleaseCandidateInventoryEntry:
    if not isinstance(value, dict) or set(value) != {
        "group",
        "path",
        "type",
        "mode",
        "size_bytes",
        "sha256",
    }:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_ENTRY_INVALID")
    group = value["group"]
    relative_path = _require_safe_relative_path(
        value["path"], code="PHYSICAL_RELEASE_CANDIDATE_MANIFEST_PATH_INVALID"
    )
    if not isinstance(group, str) or _GROUP_RE.fullmatch(group) is None:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_GROUP_INVALID")
    if relative_path in RETIRED_PAIRED_ARVAN_S3_RUNTIME_PATHS:
        _fail("PHYSICAL_RELEASE_CANDIDATE_RETIRED_PAIRED_RUNTIME_FORBIDDEN")
    if relative_path in RETIRED_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_PATHS:
        _fail("PHYSICAL_RELEASE_CANDIDATE_V1_SINGLE_OBJECT_BASE_BACKUP_FORBIDDEN")
    if _REVIEWED_GROUP_BY_PATH.get(relative_path) != group:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_SELECTION_INVALID")
    if value["type"] != "regular" or value["mode"] not in {"000644", "000755"}:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_FILE_METADATA_INVALID")
    if not isinstance(value["size_bytes"], int) or not 0 <= value["size_bytes"] <= _MAX_ENTRY_BYTES:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_SIZE_INVALID")
    if not isinstance(value["sha256"], str) or _HEX64_RE.fullmatch(value["sha256"]) is None:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_DIGEST_INVALID")
    return PhysicalReleaseCandidateInventoryEntry(
        group=group,
        relative_path=relative_path,
        file_type="regular",
        mode=value["mode"],
        size_bytes=value["size_bytes"],
        sha256=value["sha256"],
    )


def parse_physical_release_candidate_inventory(
    canonical_manifest: bytes,
) -> PhysicalReleaseCandidateInventory:
    """Parse only the canonical, complete, non-authorizing inventory form."""

    if not isinstance(canonical_manifest, bytes) or not canonical_manifest.endswith(b"\n"):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_ENCODING_INVALID")
    try:
        text = canonical_manifest[:-1].decode("ascii")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhysicalReleaseCandidateInventoryError(
            "PHYSICAL_RELEASE_CANDIDATE_MANIFEST_ENCODING_INVALID"
        ) from exc
    expected_fields = {
        "schema",
        "status",
        "baseline_release_sha",
        "baseline_git_tree_id",
        "source_dirty_at_capture",
        "entries",
        "materialization_authorized",
        "release_authorized",
        "execution_authorized",
        "manifest_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_FIELDS_INVALID")
    if (
        value["schema"] != PHYSICAL_RELEASE_CANDIDATE_INVENTORY_SCHEMA
        or value["status"] != "draft-unsealed-staging-inventory-not-materialized"
        or value["baseline_release_sha"] != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_SHA
        or value["baseline_git_tree_id"] != FIXED_PHYSICAL_RELEASE_CANDIDATE_BASELINE_TREE
        or not isinstance(value["source_dirty_at_capture"], bool)
        or value["materialization_authorized"] is not False
        or value["release_authorized"] is not False
        or value["execution_authorized"] is not False
    ):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_BINDING_INVALID")
    if not isinstance(value["entries"], list):
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_ENTRIES_INVALID")
    entries = tuple(_parse_entry(entry) for entry in value["entries"])
    actual_selection = tuple((entry.group, entry.relative_path) for entry in entries)
    if actual_selection != REVIEWED_PHYSICAL_RELEASE_CANDIDATE_PATHS:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_SELECTION_INCOMPLETE")
    if sum(entry.size_bytes for entry in entries) > _MAX_TOTAL_BYTES:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_TOTAL_SIZE_INVALID")
    manifest_sha256 = value["manifest_sha256"]
    if not isinstance(manifest_sha256, str) or _HEX64_RE.fullmatch(manifest_sha256) is None:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_DIGEST_INVALID")
    body = dict(value)
    del body["manifest_sha256"]
    if hashlib.sha256(_canonical_json(body)).hexdigest() != manifest_sha256:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_DIGEST_MISMATCH")
    if _canonical_json(value) + b"\n" != canonical_manifest:
        _fail("PHYSICAL_RELEASE_CANDIDATE_MANIFEST_NONCANONICAL")
    return PhysicalReleaseCandidateInventory(
        canonical_manifest=canonical_manifest,
        manifest_sha256=manifest_sha256,
        baseline_release_sha=value["baseline_release_sha"],
        baseline_git_tree_id=value["baseline_git_tree_id"],
        source_dirty_at_capture=value["source_dirty_at_capture"],
        entries=entries,
    )


def verify_physical_release_candidate_inventory(
    *,
    inventory: PhysicalReleaseCandidateInventory,
    config: PhysicalReleaseCandidateInventoryConfig,
    source_inspector: PhysicalReleaseCandidateSourceInspector,
    file_reader: PhysicalReleaseCandidateFileReader,
) -> None:
    """Re-read exact selected bytes and reject a changed or unsafe staging source."""

    parsed = parse_physical_release_candidate_inventory(inventory.canonical_manifest)
    if parsed.manifest_sha256 != inventory.manifest_sha256:
        _fail("PHYSICAL_RELEASE_CANDIDATE_INVENTORY_OBJECT_MISMATCH")
    rebuilt = build_physical_release_candidate_inventory(
        config=config,
        source_inspector=source_inspector,
        file_reader=file_reader,
    )
    if rebuilt.canonical_manifest != inventory.canonical_manifest:
        _fail("PHYSICAL_RELEASE_CANDIDATE_INVENTORY_SOURCE_HASH_MISMATCH")


def verify_clean_physical_release_candidate_base(
    *,
    config: PhysicalReleaseCandidateInventoryConfig,
    source_inspector: PhysicalReleaseCandidateSourceInspector,
) -> None:
    """Verify only the clean baseline destination before a separate copier runs.

    This is deliberately not a materializer: it neither opens a candidate file
    nor creates a worktree.  It gives a future materializer one fail-closed
    predicate for refusing a dirty, misbased, unsafe, or unstable target.
    """

    source_root = _validate_config(config)
    if config.allow_dirty_staging_source:
        _fail("PHYSICAL_RELEASE_CANDIDATE_CLEAN_BASE_REQUIRES_STRICT_MODE")
    inspection = source_inspector.inspect_source(source_root=source_root)
    _validate_source_inspection(
        inspection, source_root=source_root, require_clean=True
    )
