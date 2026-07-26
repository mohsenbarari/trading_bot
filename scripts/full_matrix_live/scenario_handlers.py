"""Exact live doers and independent oracles for implemented Matrix recipes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from types import SimpleNamespace
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.dr_projection_policy import projection_version_decision
from core.secure_file_io import write_secure_atomic_bytes
from core.dr_receipt_policy import decide_receipt_sequence
from core.dr_effect_policy import effect_epoch_decision
from core.dr_failover_recovery_policy import expired_plan_recovery_decision
from core.dr_readiness_policy import (
    data_plane_readiness_reasons,
    source_tail_application_reasons,
)
from core.dr_transaction_policy import decide_transaction_group
from core.dr_blob_plane import (
    _discard_staged_blob,
    _publish_staged_blob,
    stage_content_addressed_bytes,
)
from core.dr_delivery_worker import _update_delivery_from_result
from core.dr_outbox_commit_policy import outbox_commit_action
from core.dr_failover_fault_policy import (
    connectivity_vote_decision,
    partition_delivery_decision,
    provider_mutation_recovery,
    route_verification_decision,
    transition_mutation_gate,
    transition_reservation_decision,
)
from core.dr_recovery_scenario_policy import (
    connection_drain_decision,
    database_blob_reconcile_decision,
    final_write_barrier_decision,
    recovery_writer_decision,
)
from core.dr_full_matrix_runtime_policy import (
    CapacityWatermarks,
    ambiguous_retry_decision,
    amplified_webapp_decision,
    artifact_chain_decision,
    batch_flush_decision,
    bidirectional_capacity_decision,
    capacity_watermark_decision,
    dpi_budget_decision,
    durable_drain_decision,
    healthy_link_backlog_decision,
    recovery_eta_decision,
    relay_identity_decision,
)
from core.services.trade_delivery_receipt_service import (
    classify_trade_delivery_outage,
)
from core.dr_event_receiver import (
    DrEventReceiveError,
    reserve_replay_nonce,
)
from core.dr_sync_auth import (
    DR_SYNC_PROTOCOL,
    DrSyncAuthError,
    PairwiseDrKey,
    sign_request,
    verify_request as verify_dr_request,
)
from core.sync_metadata import build_sync_metadata
from core.sync_outbox_guard import raw_sql_is_provably_read_only
from core.sync_parity import (
    build_table_parity_snapshot,
    compare_parity_snapshots,
)
from core.telegram_delivery_queue_contract import (
    InMemoryFeederCoordinator,
    InMemoryTelegramDeliveryQueue,
    TelegramDeliveryAction,
    TelegramDeliveryJob,
    TelegramDeliveryOutcome,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFeederRecord,
    TelegramHandoffInterrupted,
    apply_gateway_result,
    reconcile_ambiguous_send,
)
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    resolve_telegram_delivery_producer_mode,
    resolve_telegram_delivery_runtime,
)
from core.runtime_identity import RuntimeIdentity
from core.webapp_writer_control import (
    WriterStateSnapshot,
    snapshot_is_local_active,
)
from core.three_site_full_matrix_campaign import (
    CUSTOMER_ACTOR_PAIR_POLICIES,
    CUSTOMER_LIFECYCLE_MATRIX,
    customer_actor_pair_assertion_name,
    customer_actor_pair_contracts,
    scenarios_for_execution_class,
)
from scripts.full_matrix_live.common import (
    ROLE_AGENT_SERVICE,
    ROLE_OBSERVER_SERVICE,
    ROLE_WORKLOAD_SERVICE,
    ROLE_NAMES,
    LiveMatrixError,
    collect_all_host_snapshots,
    hash_summary,
    json_bytes,
    run_compose_db_command,
    run_compose_role_service,
    run_role_agent_operation,
    run_role_command,
    safe_read,
    strict_object,
)
from scripts.build_three_site_sync_timing_evidence import (  # noqa: E402
    MANIFEST_SCHEMA as TIMING_MANIFEST_SCHEMA,
    TimingBuildError,
    build as build_timing_evidence,
)
from core.three_site_sync_timing import (  # noqa: E402
    ROUTE_HOPS,
    SyncTimingEvidenceError,
    sync_timing_policy,
    verify_sync_timing_evidence,
)
from scripts.full_matrix_live.object_storage_protocol import (
    ObjectStorageProtocolError,
    build_request as build_object_request,
    public_key_b64,
    public_key_id,
    verify_request as verify_object_request,
)
from scripts.full_matrix_live.failover_coordinator import (  # noqa: E402
    execute_transition,
)
from scripts.full_matrix_live.recipes import Recipe


PROBE_SCHEMA = "three-site-full-matrix-site-probe-v1"
ORIGIN_LOCAL_PROBE_SCHEMA = "three-site-full-matrix-origin-local-probe-v1"
PUBLIC_INGRESS_PROBE_SCHEMA = "three-site-full-matrix-public-ingress-probe-v1"
SCENARIO_EVIDENCE_SCHEMA = "three-site-staging-full-matrix-scenario-v2"
DATABASE_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
IMPLEMENTED_HANDLER_IDS = frozenset(
    {
        "acknowledged_source_event_absent_target_blocks_promotion",
        "backup_counts_pass_semantic_parity_fails",
        "blob_database_asymmetric_failure_resume",
        "bot_and_webapp_offers_concurrent",
        "business_event_delivery_commit_boundaries",
        "counter_double_increment_fixture",
        "claim_limiter_provider_crash_boundaries",
        "cross_service_secret_boundaries",
        "delete_update_resurrection_fixture",
        "destination_sequence_private_gap_regression",
        "duplicate_gap_out_of_order_replay",
        "duplicate_worker_stale_owner_redis_loss",
        "enqueue_commit_crash_boundaries",
        "fresh_main_queue_dr_histories_equal",
        "four_role_identity_isolated",
        "hostile_artifact_path_and_signature_denied",
        "integer_id_collision_fixtures",
        "natural_identity_cross_site_collision",
        "notifications_webpush_messenger_files",
        "provider_success_outcome_ambiguity",
        "production_host_domain_bucket_untouched",
        "production_boundaries_reverified",
        "protocol_schema_key_rotation_mismatch",
        "rate_limit_timeout_malformed_response",
        "receive_ack_apply_checkpoint_boundaries",
        "restored_old_epoch_effects_remain_fenced",
        "runtime_cutover_and_forward_rollback",
        "reconciliation_owner_loss_restart",
        "same_event_replay_is_idempotent",
        "same_sequence_hash_conflict_quarantine",
        "set_role_and_cross_role_access_denied",
        "stale_term_terminal_and_destructive_rejected",
        "table_priority_cannot_overtake_stream_sequence",
        "temporary_faults_networks_processes_removed",
        "transaction_group_partial_and_corrupt",
        "backup_restore_rehearsed",
        "least_privilege_roles_attested",
        "legacy_rollback_rehearsed",
        "legacy_staging_clone_migrated",
        "market_trade_account_admin_regression",
        "messenger_upload_download_regression",
        "queue_publication_edit_callback_private",
        "missing_or_corrupt_blob_blocks_readiness",
        "unique_ids_real_business_conflict_quarantined",
        "applied_checkpoint_conflict_effect_gates",
        "database_and_blob_final_parity",
        "queue_jobs_effects_conflicts_reconciled",
        "requests_trades_partial_settlement",
        "fake_event_and_raw_sql_bypass_denied",
        "expired_plan_only_safe_fenced_recovery",
        "startup_mutation_on_fenced_standby_rejected",
        "wrong_pairwise_identity_and_nonce_replay",
        "arvan_control_failure_rate_limit",
        "arvan_pop_split_origin_is_safe",
        "asymmetric_ack_both_directions",
        "bot_fi_webapp_fi_partition",
        "certificate_expiry_during_national_outage",
        "controller_restart_each_failover_cutpoint",
        "controller_restart_mid_arvan_mutation",
        "deployment_or_migration_during_transition_rejected",
        "dns_global_national_asymmetry",
        "duplicate_operator_commands_race",
        "iran_international_cutoff_promotes_ir",
        "object_storage_interruption",
        "one_hour_backlog_with_live_traffic",
        "twenty_four_hour_endurance_no_growth",
        "queue_work_inflight_during_promotion",
        "simultaneous_promotion_attempt_single_epoch",
        "webapp_fi_webapp_ir_partition",
        "fi_host_loss_without_national_cutoff",
        "ir_only_active_origin_loss_is_safe_unavailable",
        "permanent_fi_recovery_hub_loss",
        "power_loss_between_fence_and_enable",
        "wal_event_redis_blob_capacity_exhaustion_safe",
        "witness_partition_and_vm_pause",
        "bot_remains_active_all_outage_classes",
        "database_blob_inverse_completion_reconciles",
        "fi_epoch_reacquire_and_route_switch",
        "file_transfer_interruption_resumes_by_hash",
        "final_write_barrier_with_live_arrivals",
        "ir_remains_active_during_recovery",
        "old_http_websocket_connections_drained",
        "recovery_and_failback_restart_resume",
        "reconnect_flap_and_bounded_catchup",
        "short_medium_long_outage_rules",
        "ambiguous_client_command_retry_is_idempotent",
        "batch_flush_inflight_boundaries",
        "database_redis_blob_storage_watermarks",
        "dpi_request_byte_budget_enforced",
        "dropped_wakeup_still_durably_drains",
        "finland_directions_one_fifty_events_each",
        "healthy_link_never_accumulates_backlog",
        "recovery_eta_and_non_starvation",
        "relay_preserves_origin_without_echo",
        "webapp_dr_three_hundred_events_amplified",
        "writer_renewal_and_dr_relay_under_load",
        "three_site_sync_timing_steady_state",
        "three_hundred_rps_fifty_fifty",
        "writer_epoch_route_and_standby_final_state",
        "test_ingress_same_release_and_data_plane",
        "artifact_hash_chain_and_external_anchor",
        "cdn_dynamic_cache_and_stale_health_denied",
        "canonical_staging_domain_auth_cors_links",
        "customer_actor_matrix_normal_fi_active",
        "customer_actor_matrix_iran_active_outage",
        "customer_actor_matrix_recovery_ir_routed",
        "customer_actor_matrix_post_failback_fi_active",
        "second_cycle_same_or_stronger_oracles",
        "websocket_reconnect_and_cursor_reconcile",
        "session_failover_contract",
    }
)
CONVERGENCE_HANDLER_IDS = frozenset(
    {
        "applied_checkpoint_conflict_effect_gates",
        "database_and_blob_final_parity",
        "queue_jobs_effects_conflicts_reconciled",
    }
)
QUEUE_POLICY_FIXTURE_IDS = frozenset(
    {
        "claim_limiter_provider_crash_boundaries",
        "duplicate_worker_stale_owner_redis_loss",
        "enqueue_commit_crash_boundaries",
        "provider_success_outcome_ambiguity",
        "rate_limit_timeout_malformed_response",
        "reconciliation_owner_loss_restart",
    }
)
SECURITY_POLICY_FIXTURE_IDS = frozenset(
    {
        "fake_event_and_raw_sql_bypass_denied",
        "hostile_artifact_path_and_signature_denied",
        "protocol_schema_key_rotation_mismatch",
        "startup_mutation_on_fenced_standby_rejected",
        "wrong_pairwise_identity_and_nonce_replay",
        "restored_old_epoch_effects_remain_fenced",
        "expired_plan_only_safe_fenced_recovery",
    }
)
DR_POLICY_FIXTURE_IDS = frozenset(
    {
        "destination_sequence_private_gap_regression",
        "duplicate_gap_out_of_order_replay",
        "same_event_replay_is_idempotent",
        "same_sequence_hash_conflict_quarantine",
        "stale_term_terminal_and_destructive_rejected",
        "table_priority_cannot_overtake_stream_sequence",
    }
)
DR_FAULT_POLICY_FIXTURE_IDS = frozenset(
    {
        "acknowledged_source_event_absent_target_blocks_promotion",
        "blob_database_asymmetric_failure_resume",
        "missing_or_corrupt_blob_blocks_readiness",
        "receive_ack_apply_checkpoint_boundaries",
        "transaction_group_partial_and_corrupt",
    }
)
RELEASE_TRANSITION_POLICY_FIXTURE_IDS = frozenset(
    {
        "business_event_delivery_commit_boundaries",
        "runtime_cutover_and_forward_rollback",
    }
)
CLEANUP_LIVE_HANDLER_IDS = frozenset(
    {
        "temporary_faults_networks_processes_removed",
    }
)
TIMING_LIVE_HANDLER_IDS = frozenset(
    {
        "three_site_sync_timing_steady_state",
        "three_hundred_rps_fifty_fifty",
    }
)
RECOVERY_TIMING_LIVE_HANDLER_IDS = frozenset(
    {"reconnect_flap_and_bounded_catchup"}
)
ONE_HOUR_BACKLOG_LIVE_HANDLER_IDS = frozenset(
    {"one_hour_backlog_with_live_traffic"}
)
ENDURANCE_LIVE_HANDLER_IDS = frozenset(
    {"twenty_four_hour_endurance_no_growth"}
)
DESTRUCTIVE_WITNESS_HANDLER_IDS = frozenset(
    {"witness_partition_and_vm_pause"}
)
DESTRUCTIVE_FI_HOST_LOSS_HANDLER_IDS = frozenset(
    {"fi_host_loss_without_national_cutoff"}
)
DESTRUCTIVE_IR_ACTIVE_ORIGIN_LOSS_HANDLER_IDS = frozenset(
    {"ir_only_active_origin_loss_is_safe_unavailable"}
)
DESTRUCTIVE_FI_RECOVERY_HUB_LOSS_HANDLER_IDS = frozenset(
    {"permanent_fi_recovery_hub_loss"}
)
DESTRUCTIVE_CAPACITY_HANDLER_IDS = frozenset(
    {"wal_event_redis_blob_capacity_exhaustion_safe"}
)
ONE_HOUR_RECOVERY_BACKLOG = {
    "pause_seconds": 3600,
    "batch_count": 20,
    "samples_per_route": 100,
    "target_rps": 2.0,
    "batch_spacing_seconds": 180,
}
ENDURANCE_24H_PROFILE = {
    "duration_seconds": 86_400,
    "sample_interval_seconds": 300,
    "samples_per_route": 1,
    "target_rps": 1.0,
    # A bounded workload must never consume a material fraction of a mounted
    # staging disk in one interval or across the complete 24-hour exercise.
    "max_step_storage_loss_bytes": 128 * 1024 * 1024,
    "max_total_storage_loss_bytes": 1024 * 1024 * 1024,
    "max_rows_per_sample": 64,
}
FINAL_WRITER_HANDLER_IDS = frozenset(
    {"writer_epoch_route_and_standby_final_state"}
)
INGRESS_LIVE_HANDLER_IDS = frozenset(
    {"test_ingress_same_release_and_data_plane"}
)
ARTIFACT_ANCHOR_HANDLER_IDS = frozenset(
    {"artifact_hash_chain_and_external_anchor"}
)
CDN_LIVE_HANDLER_IDS = frozenset(
    {"cdn_dynamic_cache_and_stale_health_denied"}
)
CANONICAL_INGRESS_LIVE_HANDLER_IDS = frozenset(
    {"canonical_staging_domain_auth_cors_links"}
)
REPEATABILITY_LIVE_HANDLER_IDS = frozenset(
    {"second_cycle_same_or_stronger_oracles"}
)
MESSENGER_REGRESSION_LIVE_HANDLER_IDS = frozenset(
    {
        "messenger_upload_download_regression",
        "notifications_webpush_messenger_files",
    }
)
TELEGRAM_QUEUE_REGRESSION_LIVE_HANDLER_IDS = frozenset(
    {"queue_publication_edit_callback_private"}
)
APPLICATION_REGRESSION_LIVE_HANDLER_IDS = frozenset(
    {
        "market_trade_account_admin_regression",
        "websocket_reconnect_and_cursor_reconcile",
    }
)
CUSTOMER_ACTOR_LIVE_HANDLER_IDS = frozenset(CUSTOMER_LIFECYCLE_MATRIX)
FAILOVER_FAULT_POLICY_FIXTURE_IDS = frozenset(
    {
        "arvan_control_failure_rate_limit",
        "arvan_pop_split_origin_is_safe",
        "asymmetric_ack_both_directions",
        "bot_fi_webapp_fi_partition",
        "certificate_expiry_during_national_outage",
        "controller_restart_each_failover_cutpoint",
        "controller_restart_mid_arvan_mutation",
        "deployment_or_migration_during_transition_rejected",
        "dns_global_national_asymmetry",
        "duplicate_operator_commands_race",
        "iran_international_cutoff_promotes_ir",
        "object_storage_interruption",
        "queue_work_inflight_during_promotion",
        "simultaneous_promotion_attempt_single_epoch",
        "webapp_fi_webapp_ir_partition",
    }
)
RECOVERY_POLICY_FIXTURE_IDS = frozenset(
    {
        "bot_remains_active_all_outage_classes",
        "database_blob_inverse_completion_reconciles",
        "fi_epoch_reacquire_and_route_switch",
        "file_transfer_interruption_resumes_by_hash",
        "final_write_barrier_with_live_arrivals",
        "ir_remains_active_during_recovery",
        "old_http_websocket_connections_drained",
        "recovery_and_failback_restart_resume",
        "short_medium_long_outage_rules",
    }
)
RUNTIME_POLICY_FIXTURE_IDS = frozenset(
    {
        "ambiguous_client_command_retry_is_idempotent",
        "batch_flush_inflight_boundaries",
        "database_redis_blob_storage_watermarks",
        "dpi_request_byte_budget_enforced",
        "dropped_wakeup_still_durably_drains",
        "finland_directions_one_fifty_events_each",
        "healthy_link_never_accumulates_backlog",
        "recovery_eta_and_non_starvation",
        "relay_preserves_origin_without_echo",
        "webapp_dr_three_hundred_events_amplified",
    }
)
COMBINED_WORKLOAD_LIVE_IDS = frozenset(
    {
        "bot_and_webapp_offers_concurrent",
        "requests_trades_partial_settlement",
        "writer_renewal_and_dr_relay_under_load",
    }
)
MIGRATION_FIXTURE_IDS = frozenset(
    {
        "backup_counts_pass_semantic_parity_fails",
        "counter_double_increment_fixture",
        "delete_update_resurrection_fixture",
        "integer_id_collision_fixtures",
        "natural_identity_cross_site_collision",
        "unique_ids_real_business_conflict_quarantined",
    }
)
ACTIVE_FAULT_SCHEMA = "three-site-full-matrix-active-fault-v1"
WRITER_LIFECYCLE_SCHEMA = "three-site-full-matrix-writer-lifecycle-v1"
BACKUP_FILE_RE = re.compile(r"/tmp/fm_[0-9a-f]{20}\.(backup|schema_[ab]|data_[ab])\Z")
BACKUP_DB_RE = re.compile(r"fm_[0-9a-f]{20}_[ab]\Z")
ROLLBACK_FILE_RE = re.compile(
    r"/tmp/fm_[0-9a-f]{20}_rollback\.(backup|schema_(before|after)|data_(before|after))\Z"
)
ROLLBACK_DB_RE = re.compile(r"fm_[0-9a-f]{20}_rollback\Z")


def _release_heads() -> list[str]:
    config = Config(str(Path.cwd() / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    heads = sorted(str(value) for value in script.get_heads())
    if not heads:
        raise LiveMatrixError("release migration history has no head")
    return heads


def _site_probe(
    plan: dict[str, Any],
    role_name: str,
    *,
    observer: bool,
    operation: str,
) -> dict[str, Any]:
    service = (
        ROLE_OBSERVER_SERVICE[role_name]
        if observer
        else ROLE_AGENT_SERVICE[role_name]
    )
    result = run_compose_role_service(
        role_name,
        plan["_roles"][role_name],
        service=service,
        command=[
            "/app/scripts/full_matrix_live/site_probe.py",
            "--operation",
            operation,
        ],
        timeout=180,
    )
    try:
        payload = json.loads(
            result["stdout"],
            object_pairs_hook=strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError(f"{role_name} site probe output is invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "status", "operation", "role", "result"}
        or payload.get("schema") != PROBE_SCHEMA
        or payload.get("status") != "passed"
        or payload.get("operation") != operation
        or payload.get("role") != role_name
        or not isinstance(payload.get("result"), dict)
    ):
        raise LiveMatrixError(f"{role_name} site probe did not pass")
    return payload["result"]


def _migration_states(
    plan: dict[str, Any],
    *,
    observer: bool,
) -> dict[str, dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=len(DATABASE_ROLES)) as pool:
        futures = {
            role: pool.submit(
                _site_probe,
                plan,
                role,
                observer=observer,
                operation="migration_state",
            )
            for role in DATABASE_ROLES
        }
        return {role: futures[role].result() for role in DATABASE_ROLES}


def _observer_states(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=len(DATABASE_ROLES)) as pool:
        futures = {
            role: pool.submit(
                _site_probe,
                plan,
                role,
                observer=True,
                operation="observer_privileges",
            )
            for role in DATABASE_ROLES
        }
        return {role: futures[role].result() for role in DATABASE_ROLES}


def _convergence_states(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=len(DATABASE_ROLES)) as pool:
        futures = {
            role: pool.submit(
                _site_probe,
                plan,
                role,
                observer=True,
                operation="convergence_state",
            )
            for role in DATABASE_ROLES
        }
        return {role: futures[role].result() for role in DATABASE_ROLES}


def _wait_for_business_convergence(
    plan: dict[str, Any],
    *,
    timeout_seconds: float = 900.0,
    poll_seconds: float = 2.0,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    started = time.monotonic()
    attempts = 0
    last_error: Exception | None = None
    while time.monotonic() - started <= timeout_seconds:
        attempts += 1
        try:
            states = _convergence_states(plan)
            outcome = _convergence_outcome(
                states,
                scenario_id="database_and_blob_final_parity",
            )
            return (
                {
                    **outcome,
                    "convergence_poll_attempts": attempts,
                    "convergence_observed_seconds": round(
                        time.monotonic() - started,
                        6,
                    ),
                },
                states,
            )
        except LiveMatrixError as exc:
            last_error = exc
            time.sleep(poll_seconds)
    raise LiveMatrixError("business workload did not converge before timeout") from last_error


def _combined_workload_scenarios(scenario_id: str) -> dict[str, tuple[str, ...]]:
    if scenario_id == "bot_and_webapp_offers_concurrent":
        return {
            "bot_fi": ("CLM-001", "CLM-039", "CLM-077", "CLM-115"),
            "webapp_fi": ("CLM-002", "CLM-040", "CLM-078", "CLM-116"),
        }
    if scenario_id == "requests_trades_partial_settlement":
        return {
            "bot_fi": (
                "CLM-003", "CLM-004", "CLM-041", "CLM-042",
                "CLM-079", "CLM-080", "CLM-117", "CLM-118",
                "CLM-155", "CLM-156", "CLM-193", "CLM-194",
            ),
            "webapp_fi": (
                "CLM-021", "CLM-022", "CLM-059", "CLM-060",
                "CLM-097", "CLM-098", "CLM-135", "CLM-136",
                "CLM-173", "CLM-174", "CLM-211", "CLM-212",
            ),
        }
    if scenario_id == "writer_renewal_and_dr_relay_under_load":
        return {
            "bot_fi": ("CLM-003", "CLM-117"),
            "webapp_fi": ("CLM-021", "CLM-135"),
        }
    raise LiveMatrixError("combined workload scenario dispatch is incomplete")


def _timing_clock(plan: dict[str, Any], role_name: str) -> dict[str, Any]:
    """Take a release-bound, host-local clock reading through the role transport."""

    role = plan["_roles"][role_name]
    if role.get("transport") == "object-storage-agent":
        response = run_role_agent_operation(
            role_name,
            role,
            operation="timing_clock",
            context={},
            attempt=1,
            timeout=180,
        )
        result = response.get("result")
        payload = result.get("result") if isinstance(result, dict) else None
        clock = payload.get("clock") if isinstance(payload, dict) else None
    else:
        result = run_role_command(
            role_name,
            role,
            [
                "/usr/bin/python3",
                "-I",
                "-B",
                f"{str(role['repo_root']).rstrip('/')}/scripts/measure_three_site_host_clock.py",
                "--site",
                role_name,
                "--release-sha",
                str(plan["release_sha"]),
            ],
            timeout=180,
        )
        try:
            clock = json.loads(result["stdout"], object_pairs_hook=strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveMatrixError(f"{role_name} timing clock output is invalid") from exc
    if (
        not isinstance(clock, dict)
        or clock.get("schema") != "three-site-host-clock-v1"
        or clock.get("site") != role_name
        or clock.get("release_sha") != plan["release_sha"]
        or clock.get("synchronized") is not True
    ):
        raise LiveMatrixError(f"{role_name} timing clock is not synchronized")
    return clock


def _timing_snapshot(
    plan: dict[str, Any],
    role_name: str,
    *,
    correlation_prefix: str,
    clock: dict[str, Any],
) -> dict[str, Any]:
    role = plan["_roles"][role_name]
    if role.get("transport") == "object-storage-agent":
        response = run_role_agent_operation(
            role_name,
            role,
            operation="timing_snapshot",
            context={"correlation_prefix": correlation_prefix, "clock": clock},
            attempt=1,
            timeout=900,
        )
        result = response.get("result")
        payload = result.get("result") if isinstance(result, dict) else None
        snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
    else:
        encoded_clock = base64.urlsafe_b64encode(
            json.dumps(clock, sort_keys=True, separators=(",", ":")).encode()
        ).decode().rstrip("=")
        result = run_compose_role_service(
            role_name,
            role,
            service=ROLE_OBSERVER_SERVICE[role_name],
            command=[
                "/app/scripts/full_matrix_live/site_probe.py",
                "--operation",
                "timing_snapshot",
                "--correlation-prefix",
                correlation_prefix,
                "--clock-evidence-base64",
                encoded_clock,
            ],
            timeout=900,
        )
        try:
            envelope = json.loads(result["stdout"], object_pairs_hook=strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveMatrixError(f"{role_name} timing snapshot output is invalid") from exc
        snapshot = envelope.get("result") if isinstance(envelope, dict) else None
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema") != "three-site-staging-sync-site-snapshot-v1"
        or snapshot.get("site") != role_name
        or snapshot.get("release_sha") != plan["release_sha"]
        or snapshot.get("correlation_prefix") != correlation_prefix
    ):
        raise LiveMatrixError(f"{role_name} timing snapshot identity differs")
    return snapshot


def _timing_manifest_with_journal_durations(
    *,
    manifest: dict[str, Any],
    snapshots: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Bind each latency to immutable origin/applied timestamps, not process time."""

    policy = sync_timing_policy(str(manifest["scenario_id"]))
    if policy is None:
        raise LiveMatrixError("timing scenario has no policy")
    normalized = json.loads(json.dumps(manifest), object_pairs_hook=strict_object)
    for sample in normalized["samples"]:
        route = sample["route"]
        correlation = sample["correlation_id"]
        hops = ROUTE_HOPS[route]
        source, _first_destination = hops[0]
        _last_source, destination = hops[-1]
        events = [
            value for value in snapshots[source].get("events", [])
            if value.get("correlation_id") == correlation
        ]
        if len(events) != 1:
            raise TimingBuildError("timing source event is not uniquely observable")
        event = events[0]
        receipts = [
            value for value in snapshots[destination].get("receipts", [])
            if value.get("event_id") == event.get("event_id")
            and value.get("destination_site") == destination
            and value.get("status") == "applied"
        ]
        if len(receipts) != 1:
            raise TimingBuildError("timing final receipt is not uniquely observable")
        try:
            created = datetime.fromisoformat(str(event["created_at"]).replace("Z", "+00:00"))
            applied = datetime.fromisoformat(str(receipts[0]["applied_at"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError) as exc:
            raise TimingBuildError("timing journal timestamp is invalid") from exc
        sample["controller_observed_duration_seconds"] = round(
            max(0.0, (applied - created).total_seconds()),
            6,
        )
    return normalized


def _run_timing_emitter(
    args: Any,
    plan: dict[str, Any],
    *,
    role_name: str,
    fixture_prefix: str,
    correlation_prefix: str,
    samples_per_route: int,
    target_rps: float,
    reuse_fixture_users: bool = False,
) -> dict[str, Any]:
    if plan["_roles"][role_name].get("transport") == "object-storage-agent":
        control = run_role_agent_operation(
            role_name,
            plan["_roles"][role_name],
            operation="timing_emit",
            context={
                "fixture_prefix": fixture_prefix,
                "correlation_prefix": correlation_prefix,
                "samples_per_route": samples_per_route,
                "target_rps": target_rps,
            },
            attempt=1,
            timeout=1800,
        )
        envelope = control.get("result")
        result = envelope.get("result") if isinstance(envelope, dict) else None
        payload = result.get("emitter") if isinstance(result, dict) else None
        if (
            not isinstance(envelope, dict)
            or envelope.get("schema") != "three-site-full-matrix-site-agent-result-v1"
            or envelope.get("status") != "passed"
            or envelope.get("role") != role_name
            or envelope.get("operation") != "timing_emit"
            or not isinstance(result, dict)
            or result.get("status") != "passed"
        ):
            raise LiveMatrixError(f"{role_name} timing pull emitter did not pass")
    else:
        command = [
            "/app/scripts/full_matrix_live/timing_probe.py",
            "--role",
            role_name,
            "--fixture-prefix",
            fixture_prefix,
            "--correlation-prefix",
            correlation_prefix,
            "--samples-per-route",
            str(samples_per_route),
            "--target-rps",
            str(target_rps),
        ]
        if reuse_fixture_users:
            command.append("--reuse-fixture-users")
        result = run_compose_role_service(
            role_name,
            plan["_roles"][role_name],
            service=ROLE_WORKLOAD_SERVICE[role_name],
            command=command,
            timeout=1800,
        )
        try:
            payload = json.loads(result["stdout"], object_pairs_hook=strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveMatrixError(f"{role_name} timing emitter output is invalid") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "three-site-full-matrix-timing-emitter-v1"
        or payload.get("status") != "passed"
        or payload.get("role") != role_name
        or payload.get("fixture_prefix") != fixture_prefix
        or payload.get("correlation_prefix") != correlation_prefix
        or payload.get("sample_count") != samples_per_route * 2
        or payload.get("production_touched") is not False
        or payload.get("three_site_writer_fence") is not (
            role_name in {"webapp_fi", "webapp_ir"}
        )
        or not isinstance(payload.get("samples"), list)
    ):
        raise LiveMatrixError(f"{role_name} timing emitter did not pass exactly")
    return payload


def _recovery_delivery_fault(
    plan: dict[str, Any],
    *,
    action: str,
    fault_id: str,
) -> dict[str, str]:
    """Use only the sealed WA-IR pull primitive for a delivery reconnect fault."""

    if action not in {"pause", "resume"} or re.fullmatch(
        r"FMX_[A-Za-z0-9_]{12,96}", fault_id
    ) is None:
        raise LiveMatrixError("recovery delivery fault identity is invalid")
    control = run_role_agent_operation(
        "webapp_ir",
        plan["_roles"]["webapp_ir"],
        operation="recovery_delivery_fault",
        context={"action": action, "fault_id": fault_id},
        attempt=1,
        timeout=300,
    )
    envelope = control.get("result")
    result = envelope.get("result") if isinstance(envelope, dict) else None
    expected_phase = "paused" if action == "pause" else "resumed"
    if (
        not isinstance(envelope, dict)
        or envelope.get("schema") != "three-site-full-matrix-site-agent-result-v1"
        or envelope.get("status") != "passed"
        or envelope.get("role") != "webapp_ir"
        or envelope.get("operation") != "recovery_delivery_fault"
        or not isinstance(result, dict)
        or result != {
            "status": "passed",
            "action": action,
            "fault_id": fault_id,
            "phase": expected_phase,
        }
    ):
        raise LiveMatrixError("WA-IR recovery delivery fault response is invalid")
    return dict(result)


def _recovery_timing_cleanup(plan: dict[str, Any], *, fixture_prefix: str) -> dict[str, Any]:
    """Clean IR-origin recovery fixtures without opening a direct WA-IR path."""

    if re.fullmatch(r"FMX_[A-Za-z0-9_]{8,48}", fixture_prefix) is None:
        raise LiveMatrixError("recovery timing fixture identity is invalid")
    control = run_role_agent_operation(
        "webapp_ir",
        plan["_roles"]["webapp_ir"],
        operation="timing_cleanup",
        context={"fixture_prefix": fixture_prefix},
        attempt=1,
        timeout=1800,
    )
    envelope = control.get("result")
    result = envelope.get("result") if isinstance(envelope, dict) else None
    cleanup = result.get("cleanup") if isinstance(result, dict) else None
    if (
        not isinstance(envelope, dict)
        or envelope.get("schema") != "three-site-full-matrix-site-agent-result-v1"
        or envelope.get("status") != "passed"
        or envelope.get("role") != "webapp_ir"
        or envelope.get("operation") != "timing_cleanup"
        or not isinstance(result, dict)
        or result.get("status") != "passed"
        or not isinstance(cleanup, dict)
        or cleanup.get("schema") != "three-site-full-matrix-timing-emitter-v1"
        or cleanup.get("status") != "passed"
        or cleanup.get("action") != "cleanup"
        or cleanup.get("role") != "webapp_ir"
        or cleanup.get("fixture_prefix") != fixture_prefix
        or cleanup.get("production_touched") is not False
    ):
        raise LiveMatrixError("WA-IR recovery timing cleanup response is invalid")
    return cleanup


def _recovery_delivery_resume_emit(
    args: Any,
    plan: dict[str, Any],
    *,
    fault_id: str,
    fixture_prefix: str,
    correlation_prefix: str,
    samples_per_route: int,
    target_rps: float,
) -> dict[str, Any]:
    """Resume IR delivery and emit the fixed recovery load in one pull action."""

    if (
        re.fullmatch(r"FMX_[A-Za-z0-9_]{12,96}", fault_id) is None
        or re.fullmatch(r"FMX_[A-Za-z0-9_]{8,48}", fixture_prefix) is None
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{15,23}", correlation_prefix)
        is None
        or not 1 <= samples_per_route <= 500
        or not 0.1 <= target_rps <= 1000.0
    ):
        raise LiveMatrixError("recovery resume emit identity is invalid")
    control = run_role_agent_operation(
        "webapp_ir",
        plan["_roles"]["webapp_ir"],
        operation="recovery_delivery_resume_emit",
        context={
            "fault_id": fault_id,
            "fixture_prefix": fixture_prefix,
            "correlation_prefix": correlation_prefix,
            "samples_per_route": samples_per_route,
            "target_rps": target_rps,
        },
        attempt=1,
        timeout=1800,
    )
    envelope = control.get("result")
    result = envelope.get("result") if isinstance(envelope, dict) else None
    payload = result.get("emitter") if isinstance(result, dict) else None
    if (
        not isinstance(envelope, dict)
        or envelope.get("schema") != "three-site-full-matrix-site-agent-result-v1"
        or envelope.get("status") != "passed"
        or envelope.get("role") != "webapp_ir"
        or envelope.get("operation") != "recovery_delivery_resume_emit"
        or not isinstance(result, dict)
        or result.get("status") != "passed"
        or result.get("fault_id") != fault_id
        or result.get("phase") != "resumed_with_live_emit"
        or not isinstance(payload, dict)
        or payload.get("schema") != "three-site-full-matrix-timing-emitter-v1"
        or payload.get("status") != "passed"
        or payload.get("role") != "webapp_ir"
        or payload.get("fixture_prefix") != fixture_prefix
        or payload.get("correlation_prefix") != correlation_prefix
        or payload.get("sample_count") != samples_per_route * 2
        or payload.get("three_site_writer_fence") is not True
        or payload.get("production_touched") is not False
        or not isinstance(payload.get("samples"), list)
    ):
        raise LiveMatrixError("WA-IR recovery resume emit response is invalid")
    return payload


def _timing_cleanup(plan: dict[str, Any], *, fixture_prefix: str) -> dict[str, Any]:
    def one(role_name: str) -> dict[str, Any]:
        result = run_compose_role_service(
            role_name,
            plan["_roles"][role_name],
            service=ROLE_WORKLOAD_SERVICE[role_name],
            command=[
                "/app/scripts/full_matrix_live/timing_probe.py",
                "--role", role_name,
                "--fixture-prefix", fixture_prefix,
                "--cleanup-only",
            ],
            timeout=1800,
        )
        payload = json.loads(result["stdout"], object_pairs_hook=strict_object)
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "three-site-full-matrix-timing-emitter-v1"
            or payload.get("status") != "passed"
            or payload.get("action") != "cleanup"
            or payload.get("role") != role_name
            or payload.get("fixture_prefix") != fixture_prefix
            or payload.get("production_touched") is not False
        ):
            raise LiveMatrixError("timing cleanup did not pass exactly")
        return payload

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {role: pool.submit(one, role) for role in ("bot_fi", "webapp_fi")}
        return {role: futures[role].result() for role in futures}


def _timing_live_observation(
    args: Any,
    plan: dict[str, Any],
    scenario_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = sync_timing_policy(scenario_id)
    if policy is None or scenario_id not in TIMING_LIVE_HANDLER_IDS:
        raise LiveMatrixError("timing handler dispatch is incomplete")
    samples_per_route = int(policy["minimum_samples_per_route"])
    token = args.operation_id.replace("-", "")[:12]
    fixture_prefix = f"FMX_{token}_TIM"
    correlation_prefix = f"fmxtiming:{token}"
    total_target_rps = max(40.0, float(policy["minimum_observed_rps"]))
    target_rps = total_target_rps / 2.0
    _write_state(
        plan,
        {
            "schema": ACTIVE_FAULT_SCHEMA,
            "kind": "timing_probe",
            "operation_id": args.operation_id,
            "scenario_id": scenario_id,
            "fixture_prefix": fixture_prefix,
            "correlation_prefix": correlation_prefix,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            role: pool.submit(
                _run_timing_emitter,
                args,
                plan,
                role_name=role,
                fixture_prefix=fixture_prefix,
                correlation_prefix=correlation_prefix,
                samples_per_route=samples_per_route,
                target_rps=target_rps,
            )
            for role in ("bot_fi", "webapp_fi")
        }
        emitted = {role: futures[role].result() for role in futures}
    manifest = {
        "schema": TIMING_MANIFEST_SCHEMA,
        "scenario_id": scenario_id,
        "release_sha": plan["release_sha"],
        "state": policy["state"],
        "correlation_prefix": correlation_prefix,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "load": {
            "target_requests_per_second": total_target_rps,
            "observed_requests_per_second": 0.0,
            "window_seconds": 0.0,
            "request_count": samples_per_route * 4,
            "bot_request_count": samples_per_route * 2,
            "webapp_request_count": samples_per_route * 2,
        },
        "samples": [
            {
                key: sample[key]
                for key in ("sample_id", "correlation_id", "route", "controller_observed_duration_seconds")
            }
            for payload in emitted.values()
            for sample in payload["samples"]
        ],
        "backlog": {},
    }
    deadline = time.monotonic() + 900.0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        with ThreadPoolExecutor(max_workers=3) as pool:
            clocks = {
                role: pool.submit(_timing_clock, plan, role)
                for role in DATABASE_ROLES
            }
            clock_results = {role: clocks[role].result() for role in DATABASE_ROLES}
        with ThreadPoolExecutor(max_workers=3) as pool:
            snapshots = {
                role: pool.submit(
                    _timing_snapshot,
                    plan,
                    role,
                    correlation_prefix=correlation_prefix,
                    clock=clock_results[role],
                )
                for role in DATABASE_ROLES
            }
            snapshot_results = {role: snapshots[role].result() for role in DATABASE_ROLES}
        try:
            attempt_manifest = _timing_manifest_with_journal_durations(
                manifest=manifest,
                snapshots=snapshot_results,
            )
            attempt_manifest["captured_at"] = datetime.now(timezone.utc).isoformat()
            attempt_manifest["backlog"] = {
                "required": False,
                "peak_pending_events": 0,
                "initial_oldest_age_seconds": 0.0,
                "drained_to_zero": True,
                "drain_duration_seconds": 0.0,
                "live_ingress_events": 0,
                "applied_events": sum(len(value.get("receipts", [])) for value in snapshot_results.values()),
                "samples": [],
            }
            started = min(float(value["started_epoch"]) for value in emitted.values())
            finished = max(float(value["finished_epoch"]) for value in emitted.values())
            window = max(0.001, finished - started)
            attempt_manifest["load"].update(
                {
                    "observed_requests_per_second": round(
                        int(attempt_manifest["load"]["request_count"]) / window,
                        6,
                    ),
                    "window_seconds": round(window, 6),
                }
            )
            artifact = build_timing_evidence(
                manifest=attempt_manifest,
                snapshots=snapshot_results,
                scenario_id=scenario_id,
            )
            break
        except (TimingBuildError, SyncTimingEvidenceError) as exc:
            last_error = exc
            time.sleep(2.0)
    else:
        raise LiveMatrixError("timing probes did not converge before timeout") from last_error
    outcome = {
        "sample_count": len(artifact["samples"]),
        "summary": artifact["summary"],
        "sample_sha256": hash_summary(artifact["samples"]),
    }
    observations = {
        "fixture_prefix": fixture_prefix,
        "correlation_prefix": correlation_prefix,
        "emitted": emitted,
        "manifest": attempt_manifest,
        "artifact": artifact,
    }
    return outcome, observations


def _timing_live_verify(
    args: Any,
    plan: dict[str, Any],
    scenario_id: str,
    runner: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = runner.get("expected_outcome")
    retained = runner.get("doer_observations")
    if (
        not isinstance(expected, dict)
        or not isinstance(retained, dict)
        or not isinstance(retained.get("manifest"), dict)
        or not isinstance(retained.get("artifact"), dict)
    ):
        raise LiveMatrixError("timing doer evidence is incomplete")
    state = _validate_timing_probe_state(_read_state(plan))
    if state["operation_id"] != args.operation_id or state["scenario_id"] != scenario_id:
        raise LiveMatrixError("timing cleanup state belongs to another operation")
    prefix = state["correlation_prefix"]
    manifest = retained["manifest"]
    if manifest.get("correlation_prefix") != prefix:
        raise LiveMatrixError("timing retained manifest differs from cleanup state")
    with ThreadPoolExecutor(max_workers=3) as pool:
        clocks = {role: pool.submit(_timing_clock, plan, role) for role in DATABASE_ROLES}
        clock_results = {role: clocks[role].result() for role in DATABASE_ROLES}
    with ThreadPoolExecutor(max_workers=3) as pool:
        snapshots = {
            role: pool.submit(
                _timing_snapshot,
                plan,
                role,
                correlation_prefix=prefix,
                clock=clock_results[role],
            )
            for role in DATABASE_ROLES
        }
        snapshot_results = {role: snapshots[role].result() for role in DATABASE_ROLES}
    independent_manifest = _timing_manifest_with_journal_durations(
        manifest=manifest,
        snapshots=snapshot_results,
    )
    independent_manifest["captured_at"] = datetime.now(timezone.utc).isoformat()
    independent_manifest["backlog"] = {
        "required": False,
        "peak_pending_events": 0,
        "initial_oldest_age_seconds": 0.0,
        "drained_to_zero": True,
        "drain_duration_seconds": 0.0,
        "live_ingress_events": 0,
        "applied_events": sum(len(value.get("receipts", [])) for value in snapshot_results.values()),
        "samples": [],
    }
    started = min(float(value["started_epoch"]) for value in retained["emitted"].values())
    finished = max(float(value["finished_epoch"]) for value in retained["emitted"].values())
    window = max(0.001, finished - started)
    independent_manifest["load"].update(
        {
            "observed_requests_per_second": round(
                int(independent_manifest["load"]["request_count"]) / window,
                6,
            ),
            "window_seconds": round(window, 6),
        }
    )
    artifact = build_timing_evidence(
        manifest=independent_manifest,
        snapshots=snapshot_results,
        scenario_id=scenario_id,
    )
    verify_sync_timing_evidence(artifact, scenario_id=scenario_id)
    observed = {
        "sample_count": len(artifact["samples"]),
        "summary": artifact["summary"],
        "sample_sha256": hash_summary(artifact["samples"]),
    }
    if observed != expected:
        raise LiveMatrixError("independent timing evidence differs from the doer")
    cleanup = _cleanup_timing_probe(plan, _read_state(plan))
    artifact_path = args.artifact_root / f"{args.operation_id}-sync-timing.json"
    if artifact_path.exists() or artifact_path.is_symlink():
        raise LiveMatrixError("timing evidence path already exists")
    artifact_raw = (
        json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    write_secure_atomic_bytes(
        artifact_path,
        artifact_raw,
        label="Full Matrix retained synchronization timing evidence",
        mode=0o600,
        max_size=32 * 1024 * 1024,
    )
    evidence = {
        "path": artifact_path.name,
        "sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "size": len(artifact_raw),
    }
    return observed, {
        "manifest": independent_manifest,
        "artifact": artifact,
        "cleanup": cleanup,
        "timing_evidence": evidence,
    }


def _recovery_timing_state(value: dict[str, Any]) -> dict[str, str]:
    fields = {
        "schema", "kind", "operation_id", "scenario_id", "fault_id",
        "fixture_first", "fixture_second", "fixture_live", "correlation_prefix",
        "phase", "created_at",
    }
    if (
        set(value) != fields
        or value.get("schema") != ACTIVE_FAULT_SCHEMA
        or value.get("kind") != "recovery_timing_probe"
        or value.get("scenario_id") not in RECOVERY_TIMING_LIVE_HANDLER_IDS
        or re.fullmatch(r"[0-9a-f-]{36}", str(value.get("operation_id") or "")) is None
        or re.fullmatch(r"FMX_[A-Za-z0-9_]{12,96}", str(value.get("fault_id") or "")) is None
        or any(
            re.fullmatch(r"FMX_[A-Za-z0-9_]{8,48}", str(value.get(field) or ""))
            is None
            for field in ("fixture_first", "fixture_second", "fixture_live")
        )
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{15,23}",
            str(value.get("correlation_prefix") or ""),
        )
        is None
        or value.get("phase")
        not in {"prepared", "first_paused", "first_resumed", "second_paused", "live_emitted"}
    ):
        raise LiveMatrixError("recovery timing active state is invalid")
    return {key: str(item) for key, item in value.items()}


def _one_hour_backlog_state(value: dict[str, Any]) -> dict[str, Any]:
    """Validate the durable recovery record for the 60-minute paused window."""

    fields = {
        "schema", "kind", "operation_id", "scenario_id", "fault_id",
        "batch_fixtures", "correlation_prefix", "live_fixture", "phase",
        "completed_batch_count", "pause_started_at", "created_at",
    }
    fixtures = value.get("batch_fixtures") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != ACTIVE_FAULT_SCHEMA
        or value.get("kind") != "one_hour_recovery_backlog"
        or value.get("scenario_id") != "one_hour_backlog_with_live_traffic"
        or re.fullmatch(r"[0-9a-f-]{36}", str(value.get("operation_id") or "")) is None
        or re.fullmatch(r"FMX_[A-Za-z0-9_]{12,96}", str(value.get("fault_id") or "")) is None
        or not isinstance(fixtures, list)
        or len(fixtures) != len(_one_hour_backlog_schedule())
        or len({str(item) for item in fixtures}) != len(fixtures)
        or any(
            re.fullmatch(r"FMX_[A-Za-z0-9_]{8,48}", str(item)) is None
            for item in fixtures
        )
        or re.fullmatch(
            r"FMX_[A-Za-z0-9_]{8,48}", str(value.get("live_fixture") or "")
        )
        is None
        or str(value.get("live_fixture")) in {str(item) for item in fixtures}
        or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{15,23}",
            str(value.get("correlation_prefix") or ""),
        )
        is None
        or value.get("phase") not in {"prepared", "paused", "emitting", "resumed"}
        or type(value.get("completed_batch_count")) is not int
        or not 0 <= int(value["completed_batch_count"]) <= len(fixtures)
    ):
        raise LiveMatrixError("one-hour recovery backlog state is invalid")
    _utc_snapshot(value.get("pause_started_at"), label="one-hour pause start")
    _utc_snapshot(value.get("created_at"), label="one-hour state creation")
    return {
        **value,
        "batch_fixtures": [str(item) for item in fixtures],
        "fault_id": str(value["fault_id"]),
        "live_fixture": str(value["live_fixture"]),
        "correlation_prefix": str(value["correlation_prefix"]),
    }


def _recovery_writer_precondition(plan: dict[str, Any]) -> dict[str, Any]:
    lifecycle = _writer_lifecycle_state(plan)
    pair = _writer_pair_observation(plan)
    _assert_writer_pair(
        pair,
        active_site="webapp_ir",
        writer_epoch=int(lifecycle["writer_epoch_after"]),
    )
    ingress = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    return {
        "writer_lifecycle": lifecycle,
        "writer_pair": pair,
        "public_ingress": ingress,
    }


def _one_hour_backlog_schedule() -> tuple[dict[str, int | float], ...]:
    """Return the closed, bounded one-hour IR-origin workload schedule.

    Each batch has its own fixture/correlation namespace.  This keeps each
    Object-Storage response and each independent snapshot within the bounded
    evidence channel while the aggregate is still above the policy's one-RPS
    live-traffic floor for the complete retained one-hour pause.
    """

    profile = ONE_HOUR_RECOVERY_BACKLOG
    if (
        set(profile)
        != {
            "pause_seconds", "batch_count", "samples_per_route", "target_rps",
            "batch_spacing_seconds",
        }
        or type(profile["pause_seconds"]) is not int
        or type(profile["batch_count"]) is not int
        or type(profile["samples_per_route"]) is not int
        or type(profile["batch_spacing_seconds"]) is not int
        or type(profile["target_rps"]) not in {int, float}
    ):
        raise LiveMatrixError("one-hour recovery backlog profile is invalid")
    pause = int(profile["pause_seconds"])
    batches = int(profile["batch_count"])
    samples = int(profile["samples_per_route"])
    target = float(profile["target_rps"])
    spacing = int(profile["batch_spacing_seconds"])
    # Two fixed IR-origin routes are emitted by each timing-probe invocation.
    aggregate_rps = (batches * samples * 2) / pause
    if (
        pause != 3600
        or batches != 20
        or samples != 100
        or target != 2.0
        or spacing != 180
        or aggregate_rps < 1.0
        or (samples * 2) / target > spacing
        or (batches - 1) * spacing + (samples * 2) / target > pause
    ):
        raise LiveMatrixError("one-hour recovery backlog profile cannot prove live traffic")
    return tuple(
        {
            "index": index,
            "start_after_seconds": index * spacing,
            "samples_per_route": samples,
            "target_rps": target,
        }
        for index in range(batches)
    )


def _recovery_snapshot_set(
    plan: dict[str, Any],
    *,
    correlation_prefix: str,
) -> dict[str, dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=3) as pool:
        clocks = {role: pool.submit(_timing_clock, plan, role) for role in DATABASE_ROLES}
        clock_results = {role: clocks[role].result() for role in DATABASE_ROLES}
    with ThreadPoolExecutor(max_workers=3) as pool:
        snapshots = {
            role: pool.submit(
                _timing_snapshot,
                plan,
                role,
                correlation_prefix=correlation_prefix,
                clock=clock_results[role],
            )
            for role in DATABASE_ROLES
        }
        return {role: snapshots[role].result() for role in DATABASE_ROLES}


def _utc_snapshot(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise LiveMatrixError(f"recovery timing {label} is invalid") from exc
    if parsed.tzinfo is None:
        raise LiveMatrixError(f"recovery timing {label} lacks a timezone")
    return parsed.astimezone(timezone.utc)


def _pending_backlog_snapshot(
    snapshots: dict[str, dict[str, Any]],
    *,
    correlation_prefix: str,
) -> dict[str, Any]:
    source = snapshots.get("webapp_ir")
    if not isinstance(source, dict) or source.get("correlation_prefix") != correlation_prefix:
        raise LiveMatrixError("recovery timing source snapshot is invalid")
    backlog = source.get("backlog")
    if (
        not isinstance(backlog, dict)
        or type(backlog.get("pending_events")) is not int
        or int(backlog["pending_events"]) <= 0
        or not isinstance(backlog.get("oldest_pending_at"), str)
    ):
        raise LiveMatrixError("recovery delivery pause did not create a durable backlog")
    captured = _utc_snapshot(source.get("captured_at"), label="positive backlog capture")
    oldest = _utc_snapshot(backlog["oldest_pending_at"], label="oldest pending event")
    if oldest > captured + timedelta(seconds=1):
        raise LiveMatrixError("recovery backlog oldest timestamp is in the future")
    return {
        "captured_at": captured,
        "pending_events": int(backlog["pending_events"]),
        "oldest_age_seconds": round(max(0.0, (captured - oldest).total_seconds()), 6),
        "snapshot_sha256": hash_summary(snapshots),
    }


def _emitter_samples(
    emitted: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for label, payload in emitted.items():
        raw = payload.get("samples")
        if not isinstance(raw, list):
            raise LiveMatrixError("recovery timing emitter samples are missing")
        for item in raw:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("sample_id"), str)
                or not isinstance(item.get("correlation_id"), str)
                or not isinstance(item.get("route"), str)
            ):
                raise LiveMatrixError("recovery timing emitter sample is invalid")
            samples.append(
                {
                    "sample_id": f"{label}:{item['sample_id']}",
                    "correlation_id": item["correlation_id"],
                    "route": item["route"],
                    "controller_observed_duration_seconds": item.get(
                        "controller_observed_duration_seconds"
                    ),
                }
            )
    if len({str(item["sample_id"]) for item in samples}) != len(samples):
        raise LiveMatrixError("recovery timing sample identities were reused")
    return samples


def _recovery_backlog_manifest(
    *,
    scenario_id: str,
    correlation_prefix: str,
    emitted: dict[str, dict[str, Any]],
    first_pending: dict[str, Any],
    second_pending: dict[str, Any],
    final_snapshots: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    source = final_snapshots.get("webapp_ir")
    if not isinstance(source, dict):
        raise LiveMatrixError("recovery final source snapshot is missing")
    final_backlog = source.get("backlog")
    if (
        not isinstance(final_backlog, dict)
        or final_backlog.get("pending_events") != 0
    ):
        raise LiveMatrixError("recovery backlog did not drain to zero")
    final_captured = _utc_snapshot(source.get("captured_at"), label="final backlog capture")
    samples = [first_pending, second_pending]
    if final_captured <= samples[-1]["captured_at"]:
        raise LiveMatrixError("recovery backlog observation order is invalid")
    all_samples = _emitter_samples(emitted)
    emitted_ids = {
        str(item["correlation_id"])
        for item in all_samples
    }
    source_events = source.get("events")
    if not isinstance(source_events, list):
        raise LiveMatrixError("recovery final source events are missing")
    event_by_correlation = {
        str(item.get("correlation_id")): item
        for item in source_events
        if isinstance(item, dict) and str(item.get("correlation_id")) in emitted_ids
    }
    if set(event_by_correlation) != emitted_ids:
        raise LiveMatrixError("recovery timing event evidence is incomplete")
    live_correlations = {
        str(item["correlation_id"])
        for item in emitted["live"].get("samples", [])
        if isinstance(item, dict)
    }
    if not live_correlations or not live_correlations.issubset(event_by_correlation):
        raise LiveMatrixError("recovery live ingress is not observable")
    second_correlations = {
        str(item["correlation_id"])
        for item in emitted["second"].get("samples", [])
        if isinstance(item, dict)
    }
    second_ids = {
        str(event_by_correlation[item].get("event_id")) for item in second_correlations
    }
    live_created = min(
        _utc_snapshot(event_by_correlation[item].get("created_at"), label="live event")
        for item in live_correlations
    )
    second_acknowledged: list[datetime] = []
    applied_events: set[str] = set()
    for snapshot in final_snapshots.values():
        receipts = snapshot.get("receipts") if isinstance(snapshot, dict) else None
        if not isinstance(receipts, list):
            raise LiveMatrixError("recovery final receipt evidence is missing")
        for receipt in receipts:
            if not isinstance(receipt, dict):
                continue
            event_id = str(receipt.get("event_id") or "")
            if event_id in {str(value.get("event_id")) for value in event_by_correlation.values()}:
                if receipt.get("status") == "applied":
                    applied_events.add(event_id)
    deliveries = source.get("deliveries")
    if not isinstance(deliveries, list):
        raise LiveMatrixError("recovery final delivery evidence is missing")
    for delivery in deliveries:
        if (
            isinstance(delivery, dict)
            and str(delivery.get("event_id") or "") in second_ids
            and isinstance(delivery.get("acknowledged_at"), str)
        ):
            second_acknowledged.append(
                _utc_snapshot(delivery["acknowledged_at"], label="second flap acknowledgement")
            )
    if not second_acknowledged or max(second_acknowledged) < live_created:
        raise LiveMatrixError("live ingress did not overlap recovery backlog drain")
    peak = max(samples, key=lambda item: int(item["pending_events"]))
    first_captured = samples[0]["captured_at"]
    drain_seconds = round((final_captured - first_captured).total_seconds(), 6)
    if drain_seconds <= 0:
        raise LiveMatrixError("recovery backlog drain duration is not positive")
    first_count = len(emitted["first"].get("samples", []))
    second_count = len(emitted["second"].get("samples", []))
    live_count = len(live_correlations)
    intervals = [
        max(0.001, (samples[0]["captured_at"] - first_captured).total_seconds()),
        max(0.001, (samples[1]["captured_at"] - samples[0]["captured_at"]).total_seconds()),
        max(0.001, (final_captured - samples[1]["captured_at"]).total_seconds()),
    ]
    backlog_samples = [
        {
            "observed_at": samples[0]["captured_at"].isoformat(),
            "pending_events": int(samples[0]["pending_events"]),
            "oldest_age_seconds": float(samples[0]["oldest_age_seconds"]),
            "ingress_events_per_second": round(first_count / intervals[0], 6),
            "apply_events_per_second": 0.0,
        },
        {
            "observed_at": samples[1]["captured_at"].isoformat(),
            "pending_events": int(samples[1]["pending_events"]),
            "oldest_age_seconds": float(samples[1]["oldest_age_seconds"]),
            "ingress_events_per_second": round(second_count / intervals[1], 6),
            "apply_events_per_second": 0.0,
        },
        {
            "observed_at": final_captured.isoformat(),
            "pending_events": 0,
            "oldest_age_seconds": 0.0,
            "ingress_events_per_second": round(live_count / intervals[2], 6),
            "apply_events_per_second": round(len(applied_events) / intervals[2], 6),
        },
    ]
    policy = sync_timing_policy(scenario_id)
    if policy is None:
        raise LiveMatrixError("recovery timing policy is missing")
    started = min(float(payload["started_epoch"]) for payload in emitted.values())
    finished = max(float(payload["finished_epoch"]) for payload in emitted.values())
    window = max(0.001, finished - started)
    return {
        "schema": TIMING_MANIFEST_SCHEMA,
        "scenario_id": scenario_id,
        "release_sha": str(source.get("release_sha") or ""),
        "state": policy["state"],
        "correlation_prefix": correlation_prefix,
        "captured_at": final_captured.isoformat(),
        "load": {
            "target_requests_per_second": 10.0,
            "observed_requests_per_second": round(len(all_samples) / window, 6),
            "window_seconds": round(window, 6),
            "request_count": len(all_samples),
            "bot_request_count": 0,
            "webapp_request_count": len(all_samples),
        },
        "samples": all_samples,
        "backlog": {
            "required": True,
            "peak_pending_events": int(peak["pending_events"]),
            "initial_oldest_age_seconds": float(peak["oldest_age_seconds"]),
            "drained_to_zero": True,
            "drain_duration_seconds": drain_seconds,
            "live_ingress_events": live_count,
            "applied_events": len(applied_events),
            "samples": backlog_samples,
        },
    }


def _recovery_timing_outcome(artifact: dict[str, Any]) -> dict[str, bool]:
    verify_sync_timing_evidence(artifact, scenario_id="reconnect_flap_and_bounded_catchup")
    backlog = artifact.get("backlog")
    if not isinstance(backlog, dict):
        raise LiveMatrixError("recovery timing artifact lacks backlog evidence")
    return {
        "two_closed_ir_delivery_flaps_proved": True,
        "wa_ir_remained_the_only_witness_writer": True,
        "public_ingress_remained_on_wa_ir": True,
        "durable_backlog_was_positive_then_drained": (
            int(backlog.get("peak_pending_events") or 0) > 0
            and backlog.get("drained_to_zero") is True
        ),
        "live_ir_ingress_overlapped_backlog_drain": int(
            backlog.get("live_ingress_events") or 0
        ) > 0,
        "all_recovery_sync_routes_are_journal_attested": True,
        "private_wa_ir_pull_control_only": True,
    }


def _cleanup_recovery_timing_probe(plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    values = _recovery_timing_state(state)
    # This resume is safe both for a retained remote pause and for an already
    # committed response that the controller did not receive.  It never starts
    # a service if no retained fault exists and the service is unexpectedly down.
    resume = _recovery_delivery_fault(plan, action="resume", fault_id=values["fault_id"])
    cleanups = {
        name: _recovery_timing_cleanup(plan, fixture_prefix=values[name])
        for name in ("fixture_first", "fixture_second", "fixture_live")
    }
    convergence, states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    _state_file(plan).unlink()
    return {
        "resume": resume,
        "fixture_cleanups": cleanups,
        "convergence": convergence,
        "convergence_states_sha256": hash_summary(states),
    }


def _cleanup_one_hour_recovery_backlog(plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Fail closed recovery: resume delivery, clean every exact batch, converge."""

    values = _one_hour_backlog_state(state)
    resume = _recovery_delivery_fault(plan, action="resume", fault_id=values["fault_id"])
    fixtures = [*values["batch_fixtures"], values["live_fixture"]]
    cleanups = {
        fixture: _recovery_timing_cleanup(plan, fixture_prefix=fixture)
        for fixture in fixtures
    }
    convergence, states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    _state_file(plan).unlink()
    return {
        "resume": resume,
        "fixture_cleanups": cleanups,
        "convergence": convergence,
        "convergence_states_sha256": hash_summary(states),
    }


def _wait_until_monotonic(target: float) -> None:
    """Wait for a fixed recovery schedule without a long uninterruptible sleep."""

    while True:
        remaining = target - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(60.0, remaining))


def _batch_marker(index: int) -> str:
    alphabet = "0123456789abcdefghij"
    if not 0 <= index < len(alphabet):
        raise LiveMatrixError("one-hour recovery batch marker is invalid")
    return alphabet[index]


def _batch_applied_events(
    snapshots: dict[str, dict[str, Any]],
    *,
    correlation_prefix: str,
) -> set[str]:
    """Prove one bounded paused batch reached applied state after resumption."""

    source = snapshots.get("webapp_ir")
    if not isinstance(source, dict) or source.get("correlation_prefix") != correlation_prefix:
        raise LiveMatrixError("one-hour final source snapshot is invalid")
    backlog = source.get("backlog")
    events = source.get("events")
    if (
        not isinstance(backlog, dict)
        or backlog.get("pending_events") != 0
        or not isinstance(events, list)
        or not events
    ):
        raise LiveMatrixError("one-hour batch did not drain completely")
    event_ids = {
        str(item.get("event_id")) for item in events if isinstance(item, dict)
    }
    if not event_ids:
        raise LiveMatrixError("one-hour final batch event evidence is empty")
    applied: set[str] = set()
    for snapshot in snapshots.values():
        receipts = snapshot.get("receipts") if isinstance(snapshot, dict) else None
        if not isinstance(receipts, list):
            raise LiveMatrixError("one-hour final batch receipts are missing")
        for receipt in receipts:
            if (
                isinstance(receipt, dict)
                and str(receipt.get("event_id") or "") in event_ids
                and receipt.get("status") == "applied"
            ):
                applied.add(str(receipt["event_id"]))
    if applied != event_ids:
        raise LiveMatrixError("one-hour batch event did not reach applied state")
    return applied


def _one_hour_backlog_outcome(artifact: dict[str, Any]) -> dict[str, bool]:
    verify_sync_timing_evidence(artifact, scenario_id="one_hour_backlog_with_live_traffic")
    backlog = artifact.get("backlog")
    if not isinstance(backlog, dict):
        raise LiveMatrixError("one-hour timing artifact lacks backlog evidence")
    return {
        "full_sixty_minute_delivery_pause_completed": True,
        "wa_ir_remained_the_only_witness_writer": True,
        "public_ingress_remained_on_wa_ir": True,
        "aggregate_live_ingress_exceeded_one_rps": True,
        "all_bounded_backlog_batches_drained_and_applied": True,
        "final_live_recovery_routes_are_journal_attested": True,
        "private_wa_ir_pull_control_only": True,
    }


def _run_one_hour_backlog_cycle(
    args: Any,
    plan: dict[str, Any],
    *,
    label: str,
) -> tuple[dict[str, bool], dict[str, Any]]:
    """Run the exact one-hour IR Writer backlog window, then prove drainage.

    The twenty bounded paused batches deliberately use separate prefixes.  A
    final bounded atomic-resume batch supplies raw per-hop timing evidence;
    aggregate batch receipts independently prove that all earlier backlog
    traffic drained without enlarging any one Object Storage response.
    """

    scenario_id = "one_hour_backlog_with_live_traffic"
    if label not in {"doer", "oracle"} or _state_file(plan).exists():
        raise LiveMatrixError("one-hour backlog cycle cannot start with retained state")
    precondition = _recovery_writer_precondition(plan)
    schedule = _one_hour_backlog_schedule()
    token = args.operation_id.replace("-", "")[:11]
    suffix = "d" if label == "doer" else "o"
    root = f"fmxtiming:{token}{suffix}"
    state: dict[str, Any] = {
        "schema": ACTIVE_FAULT_SCHEMA,
        "kind": "one_hour_recovery_backlog",
        "operation_id": args.operation_id,
        "scenario_id": scenario_id,
        "fault_id": f"FMX_{token}_{suffix.upper()}1HR",
        "batch_fixtures": [
            f"FMX_{token}_H{suffix.upper()}{item['index']:02d}" for item in schedule
        ],
        "correlation_prefix": root,
        "live_fixture": f"FMX_{token}_H{suffix.upper()}L",
        "phase": "prepared",
        "completed_batch_count": 0,
        "pause_started_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _one_hour_backlog_state(state)
    _write_state(plan, state)
    pending_samples: list[dict[str, Any]] = []
    emitted_batches: list[dict[str, Any]] = []
    started = time.monotonic()
    started_epoch = time.time()
    pause_seconds = int(ONE_HOUR_RECOVERY_BACKLOG["pause_seconds"])
    try:
        _recovery_delivery_fault(plan, action="pause", fault_id=state["fault_id"])
        state["phase"] = "paused"
        state["pause_started_at"] = datetime.now(timezone.utc).isoformat()
        _write_state(plan, state)
        for item in schedule:
            _wait_until_monotonic(started + float(item["start_after_seconds"]))
            index = int(item["index"])
            prefix = f"{root}{_batch_marker(index)}"
            emitted = _run_timing_emitter(
                args,
                plan,
                role_name="webapp_ir",
                fixture_prefix=state["batch_fixtures"][index],
                correlation_prefix=prefix,
                samples_per_route=int(item["samples_per_route"]),
                target_rps=float(item["target_rps"]),
            )
            snapshot = _recovery_snapshot_set(plan, correlation_prefix=prefix)
            pending = _pending_backlog_snapshot(snapshot, correlation_prefix=prefix)
            emitted_batches.append(emitted)
            pending_samples.append(pending)
            state["phase"] = "emitting"
            state["completed_batch_count"] = index + 1
            _write_state(plan, state)
        _wait_until_monotonic(started + pause_seconds)
        elapsed_pause = time.monotonic() - started
        if not pause_seconds <= elapsed_pause <= pause_seconds + 300:
            raise LiveMatrixError("one-hour delivery pause did not remain within its bound")
        live_prefix = f"{root}l"
        live = _recovery_delivery_resume_emit(
            args,
            plan,
            fault_id=state["fault_id"],
            fixture_prefix=state["live_fixture"],
            correlation_prefix=live_prefix,
            samples_per_route=100,
            target_rps=2.0,
        )
        state["phase"] = "resumed"
        _write_state(plan, state)
        applied_events: set[str] = set()
        for index in range(len(schedule)):
            prefix = f"{root}{_batch_marker(index)}"
            deadline = time.monotonic() + 900.0
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                snapshots = _recovery_snapshot_set(plan, correlation_prefix=prefix)
                try:
                    applied_events.update(
                        _batch_applied_events(snapshots, correlation_prefix=prefix)
                    )
                    break
                except LiveMatrixError as exc:
                    last_error = exc
                    time.sleep(2.0)
            else:
                raise LiveMatrixError("one-hour batch did not converge after resumption") from last_error
        deadline = time.monotonic() + 900.0
        last_error = None
        while time.monotonic() < deadline:
            live_snapshots = _recovery_snapshot_set(plan, correlation_prefix=live_prefix)
            try:
                live_applied = _batch_applied_events(
                    live_snapshots, correlation_prefix=live_prefix
                )
                final_captured = _utc_snapshot(
                    live_snapshots["webapp_ir"].get("captured_at"), label="one-hour final capture"
                )
                if final_captured <= pending_samples[-1]["captured_at"]:
                    raise LiveMatrixError("one-hour backlog sample order is invalid")
                aggregate_pending = 0
                backlog_samples = []
                for item, emitted, pending in zip(schedule, emitted_batches, pending_samples):
                    aggregate_pending += int(pending["pending_events"])
                    backlog_samples.append(
                        {
                            "observed_at": pending["captured_at"].isoformat(),
                            "pending_events": aggregate_pending,
                            "oldest_age_seconds": float(pending["oldest_age_seconds"]),
                            "ingress_events_per_second": float(item["target_rps"]),
                            "apply_events_per_second": 0.0,
                        }
                    )
                total_events = sum(int(payload["sample_count"]) for payload in emitted_batches) + int(live["sample_count"])
                full_window = max(0.001, float(live["finished_epoch"]) - started_epoch)
                observed_rps = total_events / full_window
                if observed_rps < 1.0:
                    raise LiveMatrixError("one-hour aggregate live ingress is below one RPS")
                all_applied = len(applied_events | live_applied)
                if all_applied < aggregate_pending:
                    raise LiveMatrixError("one-hour aggregate backlog receipts are incomplete")
                backlog_samples.append(
                    {
                        "observed_at": final_captured.isoformat(),
                        "pending_events": 0,
                        "oldest_age_seconds": 0.0,
                        "ingress_events_per_second": 2.0,
                        "apply_events_per_second": round(all_applied / max(0.001, full_window), 6),
                    }
                )
                manifest = {
                    "schema": TIMING_MANIFEST_SCHEMA,
                    "scenario_id": scenario_id,
                    "release_sha": plan["release_sha"],
                    "state": sync_timing_policy(scenario_id)["state"],
                    "correlation_prefix": live_prefix,
                    "captured_at": final_captured.isoformat(),
                    "load": {
                        "target_requests_per_second": 1.0,
                        "observed_requests_per_second": round(observed_rps, 6),
                        "window_seconds": round(full_window, 6),
                        "request_count": total_events,
                        "bot_request_count": 0,
                        "webapp_request_count": total_events,
                    },
                    "samples": _emitter_samples({"live": live}),
                    "backlog": {
                        "required": True,
                        "peak_pending_events": aggregate_pending,
                        "initial_oldest_age_seconds": max(
                            float(item["oldest_age_seconds"]) for item in pending_samples
                        ),
                        "drained_to_zero": True,
                        "drain_duration_seconds": round(
                            (final_captured - pending_samples[0]["captured_at"]).total_seconds(), 6
                        ),
                        "live_ingress_events": int(live["sample_count"]),
                        "applied_events": all_applied,
                        "samples": backlog_samples,
                    },
                }
                manifest = _timing_manifest_with_journal_durations(
                    manifest=manifest, snapshots=live_snapshots
                )
                artifact = build_timing_evidence(
                    manifest=manifest, snapshots=live_snapshots, scenario_id=scenario_id
                )
                break
            except (LiveMatrixError, TimingBuildError, SyncTimingEvidenceError) as exc:
                last_error = exc
                time.sleep(2.0)
        else:
            raise LiveMatrixError("one-hour final timing evidence did not converge") from last_error
        outcome = _one_hour_backlog_outcome(artifact)
        cleanup = _cleanup_one_hour_recovery_backlog(plan, _read_state(plan))
        return outcome, {
            "writer_precondition": precondition,
            "paused_batches": emitted_batches,
            "pending_samples": pending_samples,
            "live": live,
            "artifact": artifact,
            "manifest": manifest,
            "cleanup": cleanup,
        }
    except Exception:
        raise


def _one_hour_backlog_verify(
    args: Any,
    plan: dict[str, Any],
    runner: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    retained = runner.get("doer_observations")
    expected = runner.get("expected_outcome")
    if (
        not isinstance(retained, dict)
        or not isinstance(retained.get("artifact"), dict)
        or not isinstance(expected, dict)
        or _one_hour_backlog_outcome(retained["artifact"]) != expected
    ):
        raise LiveMatrixError("one-hour backlog doer evidence is incomplete")
    observed, independent = _run_one_hour_backlog_cycle(args, plan, label="oracle")
    if observed != expected:
        raise LiveMatrixError("independent one-hour backlog oracle differs from doer")
    artifact = independent.get("artifact")
    if not isinstance(artifact, dict):
        raise LiveMatrixError("independent one-hour backlog artifact is missing")
    artifact_raw = (
        json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    artifact_path = args.artifact_root / f"{args.operation_id}-one-hour-sync-timing.json"
    if artifact_path.exists() or artifact_path.is_symlink():
        raise LiveMatrixError("one-hour backlog evidence path already exists")
    write_secure_atomic_bytes(
        artifact_path,
        artifact_raw,
        label="Full Matrix retained one-hour backlog timing evidence",
        mode=0o600,
        max_size=32 * 1024 * 1024,
    )
    independent["timing_evidence"] = {
        "path": artifact_path.name,
        "sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "size": len(artifact_raw),
    }
    return observed, independent


def _endurance_schedule() -> tuple[dict[str, int | float], ...]:
    """Return the non-negotiable 24-hour, five-minute sample schedule."""

    profile = ENDURANCE_24H_PROFILE
    required = {
        "duration_seconds", "sample_interval_seconds", "samples_per_route",
        "target_rps", "max_step_storage_loss_bytes",
        "max_total_storage_loss_bytes", "max_rows_per_sample",
    }
    if (
        set(profile) != required
        or any(type(profile[name]) is not int for name in required - {"target_rps"})
        or type(profile["target_rps"]) not in {int, float}
    ):
        raise LiveMatrixError("24-hour endurance profile is invalid")
    duration = int(profile["duration_seconds"])
    interval = int(profile["sample_interval_seconds"])
    if (
        duration != 86_400
        or interval != 300
        or duration % interval
        or int(profile["samples_per_route"]) != 1
        or float(profile["target_rps"]) != 1.0
        or int(profile["max_step_storage_loss_bytes"]) <= 0
        or int(profile["max_total_storage_loss_bytes"]) < int(profile["max_step_storage_loss_bytes"])
        or int(profile["max_rows_per_sample"]) < 8
    ):
        raise LiveMatrixError("24-hour endurance profile cannot prove a bounded real run")
    return tuple(
        {
            "index": index,
            "start_after_seconds": index * interval,
            "samples_per_route": 1,
            "target_rps": 1.0,
        }
        for index in range(duration // interval)
    )


def _endurance_marker(index: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if not 0 <= index < len(alphabet) ** 2:
        raise LiveMatrixError("endurance sample marker is invalid")
    return alphabet[index // len(alphabet)] + alphabet[index % len(alphabet)]


def _endurance_state(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema", "kind", "operation_id", "scenario_id", "fixture_prefix",
        "correlation_root", "phase", "completed_sample_count", "started_at",
        "last_sample_at", "created_at",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != ACTIVE_FAULT_SCHEMA
        or value.get("kind") != "twenty_four_hour_endurance"
        or value.get("scenario_id") != "twenty_four_hour_endurance_no_growth"
        or re.fullmatch(r"[0-9a-f-]{36}", str(value.get("operation_id") or "")) is None
        or re.fullmatch(r"FMX_[A-Za-z0-9_]{8,48}", str(value.get("fixture_prefix") or "")) is None
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{15,21}", str(value.get("correlation_root") or "")) is None
        or value.get("phase") not in {"prepared", "running", "completed"}
        or type(value.get("completed_sample_count")) is not int
        or not 0 <= int(value["completed_sample_count"]) <= len(_endurance_schedule())
    ):
        raise LiveMatrixError("24-hour endurance active state is invalid")
    for name in ("started_at", "last_sample_at", "created_at"):
        _utc_snapshot(value.get(name), label=f"24-hour endurance {name}")
    return dict(value)


def _endurance_journal_path(args: Any) -> Path:
    operation_id = str(args.operation_id)
    if re.fullmatch(r"[0-9a-f-]{36}", operation_id) is None:
        raise LiveMatrixError("24-hour endurance operation identity is invalid")
    root = Path(args.artifact_root)
    if not root.is_absolute() or root.is_symlink():
        raise LiveMatrixError("24-hour endurance artifact root is unsafe")
    return root / f"{operation_id}-endurance-journal.json"


def _endurance_journal(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "operation_id", "scenario_id", "records"}
        or value.get("schema") != "three-site-full-matrix-endurance-journal-v1"
        or value.get("scenario_id") != "twenty_four_hour_endurance_no_growth"
        or re.fullmatch(r"[0-9a-f-]{36}", str(value.get("operation_id") or "")) is None
        or not isinstance(value.get("records"), list)
        or len(value["records"]) > len(_endurance_schedule())
    ):
        raise LiveMatrixError("24-hour endurance journal is invalid")
    records = value["records"]
    schedule = _endurance_schedule()
    for expected_index, record in enumerate(records):
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "index", "scheduled_elapsed_seconds", "observed_elapsed_seconds",
                "finished_at", "writer_epoch", "emitted_sample_count",
                "convergence_sha256", "host_snapshots_sha256", "database_row_counts",
                "available_storage_bytes",
            }
            or record.get("index") != expected_index
            or type(record.get("scheduled_elapsed_seconds")) is not int
            or record.get("scheduled_elapsed_seconds")
            != schedule[expected_index]["start_after_seconds"]
            or type(record.get("observed_elapsed_seconds")) not in {int, float}
            or float(record["observed_elapsed_seconds"])
            < float(record["scheduled_elapsed_seconds"])
            or type(record.get("writer_epoch")) is not int
            or int(record["writer_epoch"]) < 1
            or record.get("emitted_sample_count") != 2
            or any(
                re.fullmatch(r"[0-9a-f]{64}", str(record.get(name) or "")) is None
                for name in ("convergence_sha256", "host_snapshots_sha256")
            )
            or not isinstance(record.get("database_row_counts"), dict)
            or set(record["database_row_counts"]) != set(DATABASE_ROLES)
            or not isinstance(record.get("available_storage_bytes"), dict)
            or set(record["available_storage_bytes"]) != set(ROLE_NAMES)
            or any(type(item) is not int or item < 0 for item in record["database_row_counts"].values())
            or any(type(item) is not int or item <= 0 for item in record["available_storage_bytes"].values())
        ):
            raise LiveMatrixError("24-hour endurance journal record is invalid")
        _utc_snapshot(record.get("finished_at"), label="24-hour endurance sample finish")
    return value


def _write_endurance_journal(args: Any, value: dict[str, Any]) -> None:
    _endurance_journal(value)
    write_secure_atomic_bytes(
        _endurance_journal_path(args),
        json_bytes(value),
        label="Full Matrix 24-hour endurance journal",
        mode=0o600,
        max_size=512 * 1024,
    )


def _read_endurance_journal(args: Any) -> dict[str, Any]:
    path = _endurance_journal_path(args)
    raw = safe_read(
        path,
        label="Full Matrix 24-hour endurance journal",
        owner_only=True,
        max_size=512 * 1024,
    )
    try:
        value = json.loads(raw, object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError("24-hour endurance journal JSON is invalid") from exc
    return _endurance_journal(value)


def _storage_bytes(snapshot: dict[str, Any], *, role_name: str) -> int:
    mount = snapshot.get("mount")
    filesystems = mount.get("filesystems") if isinstance(mount, dict) else None
    if not isinstance(filesystems, list) or len(filesystems) != 1:
        raise LiveMatrixError(f"{role_name} endurance mount evidence is invalid")
    available = filesystems[0].get("avail") if isinstance(filesystems[0], dict) else None
    text = str(available or "").strip()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMGTPE]?)", text, flags=re.I)
    if match is None:
        raise LiveMatrixError(f"{role_name} endurance available storage is invalid")
    scale = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4, "P": 1024**5, "E": 1024**6}
    value = int(float(match.group(1)) * scale[match.group(2).upper()])
    if value <= 0:
        raise LiveMatrixError(f"{role_name} endurance storage is exhausted")
    return value


def _endurance_sample(
    args: Any,
    plan: dict[str, Any],
    *,
    item: dict[str, int | float],
    started: float,
    correlation_root: str,
    fixture_prefix: str,
    expected_epoch: int | None,
) -> dict[str, Any]:
    index = int(item["index"])
    prefix = f"{correlation_root}{_endurance_marker(index)}"
    emitted = _run_timing_emitter(
        args,
        plan,
        role_name="webapp_fi",
        fixture_prefix=fixture_prefix,
        correlation_prefix=prefix,
        samples_per_route=int(item["samples_per_route"]),
        target_rps=float(item["target_rps"]),
        reuse_fixture_users=True,
    )
    convergence, states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    pair = _writer_pair_observation(plan)
    epoch = int(pair["webapp_fi"]["writer_epoch"])
    _assert_writer_pair(pair, active_site="webapp_fi", writer_epoch=epoch)
    if expected_epoch is not None and epoch != expected_epoch:
        raise LiveMatrixError("24-hour endurance changed the active Writer epoch")
    snapshots = collect_all_host_snapshots(plan, args.release_sha)
    if any(
        snapshot.get("managed_fault_container_count") != 0
        or snapshot.get("managed_fault_network_count") != 0
        for snapshot in snapshots.values()
    ):
        raise LiveMatrixError("24-hour endurance found retained managed fault residue")
    rows = {
        role: int(states[role].get("database_row_count"))
        for role in DATABASE_ROLES
    }
    if any(value < 0 for value in rows.values()):
        raise LiveMatrixError("24-hour endurance database row evidence is invalid")
    return {
        "index": index,
        "scheduled_elapsed_seconds": int(item["start_after_seconds"]),
        "observed_elapsed_seconds": round(time.monotonic() - started, 6),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "writer_epoch": epoch,
        "emitted_sample_count": int(emitted["sample_count"]),
        "convergence_sha256": hash_summary({"outcome": convergence, "states": states}),
        "host_snapshots_sha256": hash_summary(snapshots),
        "database_row_counts": rows,
        "available_storage_bytes": {
            role: _storage_bytes(snapshot, role_name=role)
            for role, snapshot in snapshots.items()
        },
    }


def _endurance_outcome(
    records: list[dict[str, Any]],
    *,
    elapsed_seconds: float,
) -> dict[str, bool]:
    schedule = _endurance_schedule()
    if len(records) != len(schedule) or elapsed_seconds < int(ENDURANCE_24H_PROFILE["duration_seconds"]):
        raise LiveMatrixError("24-hour endurance did not complete its full monotonic window")
    _endurance_journal({
        "schema": "three-site-full-matrix-endurance-journal-v1",
        "operation_id": "12345678-1234-4234-9234-123456789abc",
        "scenario_id": "twenty_four_hour_endurance_no_growth",
        "records": records,
    })
    epoch = records[0]["writer_epoch"]
    step_storage = int(ENDURANCE_24H_PROFILE["max_step_storage_loss_bytes"])
    total_storage = int(ENDURANCE_24H_PROFILE["max_total_storage_loss_bytes"])
    rows_limit = int(ENDURANCE_24H_PROFILE["max_rows_per_sample"])
    for previous, current in zip(records, records[1:]):
        if float(current["observed_elapsed_seconds"]) < float(previous["observed_elapsed_seconds"]):
            raise LiveMatrixError("24-hour endurance sample clock moved backwards")
        if current["writer_epoch"] != epoch:
            raise LiveMatrixError("24-hour endurance Writer epoch is not stable")
        for role in ROLE_NAMES:
            if int(previous["available_storage_bytes"][role]) - int(current["available_storage_bytes"][role]) > step_storage:
                raise LiveMatrixError("24-hour endurance storage fell too quickly")
        for role in DATABASE_ROLES:
            if int(current["database_row_counts"][role]) - int(previous["database_row_counts"][role]) > rows_limit:
                raise LiveMatrixError("24-hour endurance database rows grew too quickly")
    for role in ROLE_NAMES:
        if int(records[0]["available_storage_bytes"][role]) - int(records[-1]["available_storage_bytes"][role]) > total_storage:
            raise LiveMatrixError("24-hour endurance storage growth exceeded the hard bound")
    return {
        "full_twenty_four_monotonic_hours_completed": True,
        "two_real_fi_writer_events_each_five_minute_interval_converged": True,
        "single_fi_writer_epoch_remained_witness_safe": True,
        "all_four_host_storage_growth_remained_bounded": True,
        "database_growth_remained_bounded_without_delivery_residue": True,
        "managed_fault_residue_remained_zero": True,
    }


def _cleanup_endurance(plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    values = _endurance_state(state)
    cleanup = _timing_cleanup(plan, fixture_prefix=str(values["fixture_prefix"]))
    convergence, states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    _state_file(plan).unlink()
    return {
        "cleanup": cleanup,
        "convergence": convergence,
        "convergence_states_sha256": hash_summary(states),
    }


def _run_endurance_cycle(args: Any, plan: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    if _state_file(plan).exists():
        raise LiveMatrixError("24-hour endurance cannot start with retained active state")
    schedule = _endurance_schedule()
    token = str(args.operation_id).replace("-", "")[:10]
    root = f"fmxtiming:{token}e"
    state: dict[str, Any] = {
        "schema": ACTIVE_FAULT_SCHEMA,
        "kind": "twenty_four_hour_endurance",
        "operation_id": args.operation_id,
        "scenario_id": "twenty_four_hour_endurance_no_growth",
        "fixture_prefix": f"FMX_{token}_ENDURANCE",
        "correlation_root": root,
        "phase": "prepared",
        "completed_sample_count": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_sample_at": datetime.now(timezone.utc).isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _endurance_state(state)
    _write_state(plan, state)
    journal = {
        "schema": "three-site-full-matrix-endurance-journal-v1",
        "operation_id": args.operation_id,
        "scenario_id": "twenty_four_hour_endurance_no_growth",
        "records": [],
    }
    _write_endurance_journal(args, journal)
    started = time.monotonic()
    expected_epoch: int | None = None
    for item in schedule:
        _wait_until_monotonic(started + float(item["start_after_seconds"]))
        record = _endurance_sample(
            args, plan, item=item, started=started, correlation_root=root,
            fixture_prefix=f"{state['fixture_prefix']}D", expected_epoch=expected_epoch,
        )
        expected_epoch = int(record["writer_epoch"])
        journal["records"].append(record)
        _write_endurance_journal(args, journal)
        state["phase"] = "running"
        state["completed_sample_count"] = len(journal["records"])
        state["last_sample_at"] = record["finished_at"]
        _write_state(plan, state)
    _wait_until_monotonic(started + int(ENDURANCE_24H_PROFILE["duration_seconds"]))
    elapsed = time.monotonic() - started
    if elapsed > 90_000:
        raise LiveMatrixError("24-hour endurance exceeded its bounded controller window")
    outcome = _endurance_outcome(journal["records"], elapsed_seconds=elapsed)
    state["phase"] = "completed"
    state["last_sample_at"] = datetime.now(timezone.utc).isoformat()
    _write_state(plan, state)
    return outcome, {
        "journal": {
            "path": _endurance_journal_path(args).name,
            "sha256": hashlib.sha256(json_bytes(journal)).hexdigest(),
            "size": len(json_bytes(journal)),
        },
        "sample_count": len(journal["records"]),
        "duration_seconds": round(elapsed, 6),
        "writer_epoch": expected_epoch,
    }


def _endurance_verify(args: Any, plan: dict[str, Any], runner: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    retained = runner.get("doer_observations")
    expected = runner.get("expected_outcome")
    state = _endurance_state(_read_state(plan))
    journal = _read_endurance_journal(args)
    if (
        state["operation_id"] != args.operation_id
        or state["phase"] != "completed"
        or state["completed_sample_count"] != len(_endurance_schedule())
        or journal["operation_id"] != args.operation_id
        or not isinstance(retained, dict)
        or not isinstance(expected, dict)
        or retained.get("journal", {}).get("sha256") != hashlib.sha256(json_bytes(journal)).hexdigest()
    ):
        raise LiveMatrixError("24-hour endurance retained evidence is incomplete")
    observed = _endurance_outcome(
        journal["records"], elapsed_seconds=float(retained.get("duration_seconds") or 0.0)
    )
    if observed != expected:
        raise LiveMatrixError("24-hour endurance oracle differs from the doer")
    # The oracle makes an independent fresh write/convergence/host observation
    # after it has validated every retained five-minute sample.  Repeating the
    # 24-hour window would double the production-like load without providing a
    # stronger validation of the completed window.
    item = {"index": 0, "start_after_seconds": 0, "samples_per_route": 1, "target_rps": 1.0}
    fresh = _endurance_sample(
        args, plan, item=item, started=time.monotonic(),
        correlation_root=f"fmxtiming:{str(args.operation_id).replace('-', '')[:10]}o",
        fixture_prefix=f"{state['fixture_prefix']}O",
        expected_epoch=int(journal["records"][-1]["writer_epoch"]),
    )
    cleanup = _cleanup_endurance(plan, state)
    return observed, {
        "retained_journal": retained["journal"],
        "endurance_journal": retained["journal"],
        "fresh_oracle_sample": fresh,
        "cleanup": cleanup,
    }


def _destructive_control(plan: dict[str, Any], args: Any) -> dict[str, Any]:
    if plan.get("execution_class") != "dedicated-host-destructive":
        raise LiveMatrixError("destructive scenario is outside the dedicated campaign")
    control = plan.get("_bindings", {}).get("destructive_control_config", {}).get("payload")
    fields = {
        "schema", "campaign_id", "gate_group_id", "execution_class",
        "release_sha", "enabled", "provider_state_file", "provider_token_file",
        "audit_root",
    }
    if (
        not isinstance(control, dict)
        or set(control) != fields
        or control.get("schema") != "three-site-full-matrix-destructive-control-v1"
        or control.get("campaign_id") != args.campaign_id
        or control.get("gate_group_id") != args.gate_group_id
        or control.get("execution_class") != "dedicated-host-destructive"
        or control.get("release_sha") != args.release_sha
        or control.get("enabled") is not True
    ):
        raise LiveMatrixError("destructive provider control is not campaign-bound")
    return dict(control)


def _destructive_power(
    args: Any,
    plan: dict[str, Any],
    *,
    scenario_id: str,
    role: str,
    action: str,
) -> dict[str, Any]:
    """Run one exact reversible Arvan power action through the plan binding."""

    from scripts.full_matrix_live.arvan_destructive_control import (
        ArvanDestructiveControlError,
        build_bound_power_intent,
        execute_bound_power_intent,
    )

    control = _destructive_control(plan, args)
    try:
        intent = build_bound_power_intent(
            control=control,
            campaign_id=args.campaign_id,
            gate_group_id=args.gate_group_id,
            release_sha=args.release_sha,
            operation_id=args.operation_id,
            scenario_id=scenario_id,
            role=role,
            action=action,
        )
        result = execute_bound_power_intent(
            intent,
            control=control,
            campaign_id=args.campaign_id,
            gate_group_id=args.gate_group_id,
            release_sha=args.release_sha,
            timeout_seconds=300.0,
            poll_seconds=2.0,
        )
    except ArvanDestructiveControlError as exc:
        raise LiveMatrixError("campaign-bound destructive provider action failed") from exc
    required = {
        "schema", "status", "intent_sha256", "role", "action",
        "before_status", "after_status", "audit_event_hash",
    }
    if (
        not isinstance(result, dict)
        or set(result) != required
        or result.get("status") != "passed"
        or result.get("role") != role
        or result.get("action") != action
        or result.get("before_status") not in {"ACTIVE", "SHUTOFF", "STOPPED", "POWERED_OFF", "OFF"}
        or not isinstance(result.get("after_status"), str)
        or re.fullmatch(r"[0-9a-f]{64}", str(result.get("intent_sha256") or "")) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(result.get("audit_event_hash") or "")) is None
    ):
        raise LiveMatrixError("destructive provider result is invalid")
    return dict(result)


def _witness_pause_state(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema", "kind", "operation_id", "scenario_id", "phase",
        "writer_epoch", "created_at",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != ACTIVE_FAULT_SCHEMA
        or value.get("kind") != "destructive_witness_vm_pause"
        or value.get("scenario_id") != "witness_partition_and_vm_pause"
        or re.fullmatch(r"[0-9a-f-]{36}", str(value.get("operation_id") or "")) is None
        or value.get("phase") not in {"prepared", "witness_powered_off", "witness_powered_on"}
        or type(value.get("writer_epoch")) is not int
        or int(value["writer_epoch"]) < 1
    ):
        raise LiveMatrixError("destructive witness pause state is invalid")
    _utc_snapshot(value.get("created_at"), label="destructive witness pause creation")
    return dict(value)


def _wait_for_witness_fence(
    plan: dict[str, Any],
    *,
    writer_epoch: int,
    timeout_seconds: float,
) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            pair = _writer_pair_observation(plan)
            fi = pair["webapp_fi"]
            ir = pair["webapp_ir"]
            if (
                fi.get("active_site") == "webapp_fi"
                and ir.get("active_site") == "webapp_fi"
                and fi.get("writer_epoch") == writer_epoch
                and ir.get("writer_epoch") == writer_epoch
                and fi.get("local_active_with_witness_lease") is False
                and ir.get("local_active_with_witness_lease") is False
            ):
                return pair
        except LiveMatrixError as exc:
            last_error = exc
        time.sleep(2.0)
    raise LiveMatrixError("witness pause did not fence both WebApp sites") from last_error


def _wait_for_witness_recovery(
    plan: dict[str, Any],
    *,
    writer_epoch: int,
    timeout_seconds: float,
) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            pair = _writer_pair_observation(plan)
            _assert_writer_pair(pair, active_site="webapp_fi", writer_epoch=writer_epoch)
            return pair
        except LiveMatrixError as exc:
            last_error = exc
        time.sleep(2.0)
    raise LiveMatrixError("witness recovery did not restore the exact FI Writer") from last_error


def _destructive_active_provider_intent(
    args: Any,
    plan: dict[str, Any],
    *,
    scenario_id: str,
    role: str,
) -> dict[str, Any]:
    """Read the exact active disposable host without sending a provider POST."""

    from scripts.full_matrix_live.arvan_destructive_control import (
        ArvanDestructiveControlError,
        build_bound_power_intent,
    )

    try:
        return build_bound_power_intent(
            control=_destructive_control(plan, args),
            campaign_id=args.campaign_id,
            gate_group_id=args.gate_group_id,
            release_sha=args.release_sha,
            operation_id=args.operation_id,
            scenario_id=scenario_id,
            role=role,
            # Building a power-off intent reads the provider and requires the
            # exact host to be ACTIVE, but does not mutate it.
            action="power-off",
        )
    except ArvanDestructiveControlError as exc:
        raise LiveMatrixError("destructive provider active re-observation failed") from exc


def _cleanup_witness_pause(args: Any, plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    values = _witness_pause_state(state)
    power_on = None
    if values["phase"] == "witness_powered_off":
        try:
            power_on = _destructive_power(
                args,
                plan,
                scenario_id="witness_partition_and_vm_pause",
                role="witness",
                action="power-on",
            )
        except LiveMatrixError:
            # The provider may have committed power-on before a controller
            # response was lost.  A fresh no-POST intent proves that exact
            # condition; any other error remains fail-closed.
            _destructive_active_provider_intent(
                args,
                plan,
                scenario_id="witness_partition_and_vm_pause",
                role="witness",
            )
    pair = _wait_for_witness_recovery(
        plan,
        writer_epoch=int(values["writer_epoch"]),
        timeout_seconds=900.0,
    )
    _state_file(plan).unlink()
    return {"power_on": power_on, "writer_pair": pair}


def _run_witness_pause(args: Any, plan: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    if _state_file(plan).exists():
        raise LiveMatrixError("witness pause cannot start with retained active state")
    before = _writer_pair_observation(plan)
    epoch = int(before["webapp_fi"]["writer_epoch"])
    _assert_writer_pair(before, active_site="webapp_fi", writer_epoch=epoch)
    ingress_before = _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    state = {
        "schema": ACTIVE_FAULT_SCHEMA,
        "kind": "destructive_witness_vm_pause",
        "operation_id": args.operation_id,
        "scenario_id": "witness_partition_and_vm_pause",
        "phase": "prepared",
        "writer_epoch": epoch,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _witness_pause_state(state)
    _write_state(plan, state)
    power_off = _destructive_power(
        args,
        plan,
        scenario_id="witness_partition_and_vm_pause",
        role="witness",
        action="power-off",
    )
    state["phase"] = "witness_powered_off"
    _write_state(plan, state)
    fenced = _wait_for_witness_fence(plan, writer_epoch=epoch, timeout_seconds=300.0)
    power_on = _destructive_power(
        args,
        plan,
        scenario_id="witness_partition_and_vm_pause",
        role="witness",
        action="power-on",
    )
    state["phase"] = "witness_powered_on"
    _write_state(plan, state)
    recovered = _wait_for_witness_recovery(plan, writer_epoch=epoch, timeout_seconds=900.0)
    ingress_after = _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    _state_file(plan).unlink()
    outcome = {
        "witness_vm_pause_fenced_both_webapp_sites_without_epoch_change": True,
        "webapp_ir_never_promoted_without_national_cutoff": True,
        "witness_recovery_restored_exact_fi_writer": True,
        "public_ingress_returned_to_fi_after_witness_recovery": True,
        "campaign_bound_provider_power_audit_completed": True,
    }
    return outcome, {
        "writer_before": before,
        "ingress_before": ingress_before,
        "power_off": power_off,
        "both_sites_fenced": fenced,
        "power_on": power_on,
        "writer_recovered": recovered,
        "ingress_after": ingress_after,
    }


def _verify_witness_pause(args: Any, plan: dict[str, Any], runner: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    expected = runner.get("expected_outcome")
    retained = runner.get("doer_observations")
    if not isinstance(expected, dict) or not isinstance(retained, dict):
        raise LiveMatrixError("witness pause retained evidence is incomplete")
    before = retained.get("writer_before")
    if not isinstance(before, dict) or type(before.get("webapp_fi", {}).get("writer_epoch")) is not int:
        raise LiveMatrixError("witness pause prior Writer evidence is invalid")
    epoch = int(before["webapp_fi"]["writer_epoch"])
    pair = _wait_for_witness_recovery(plan, writer_epoch=epoch, timeout_seconds=300.0)
    ingress = _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    provider = _destructive_active_provider_intent(
        args,
        plan,
        scenario_id="witness_partition_and_vm_pause",
        role="witness",
    )
    observed = {
        "witness_vm_pause_fenced_both_webapp_sites_without_epoch_change": True,
        "webapp_ir_never_promoted_without_national_cutoff": True,
        "witness_recovery_restored_exact_fi_writer": True,
        "public_ingress_returned_to_fi_after_witness_recovery": True,
        "campaign_bound_provider_power_audit_completed": True,
    }
    if observed != expected:
        raise LiveMatrixError("witness pause oracle differs from doer")
    return observed, {
        "fresh_writer_pair": pair,
        "fresh_ingress": ingress,
        "fresh_provider_intent_sha256": provider["intent_sha256"],
    }


def _fi_host_loss_state(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema", "kind", "operation_id", "scenario_id", "phase",
        "writer_epoch", "created_at",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != ACTIVE_FAULT_SCHEMA
        or value.get("kind") != "destructive_fi_host_loss"
        or value.get("scenario_id") != "fi_host_loss_without_national_cutoff"
        or re.fullmatch(r"[0-9a-f-]{36}", str(value.get("operation_id") or "")) is None
        or value.get("phase") not in {"prepared", "fi_powered_off", "fi_powered_on"}
        or type(value.get("writer_epoch")) is not int
        or int(value["writer_epoch"]) < 1
    ):
        raise LiveMatrixError("destructive FI host-loss state is invalid")
    _utc_snapshot(value.get("created_at"), label="destructive FI host-loss creation")
    return dict(value)


def _wait_for_ir_safe_unavailable(
    plan: dict[str, Any],
    *,
    writer_epoch: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Observe only WA-IR while WA-FI is deliberately powered off."""

    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            ir = _writer_lease_observation(plan, "webapp_ir")
            if (
                ir.get("active_site") == "webapp_fi"
                and ir.get("writer_epoch") == writer_epoch
                and ir.get("control_state") == "active"
                and ir.get("local_active_with_witness_lease") is False
                and ir.get("local_active_reasons") == ["writer_active_site_mismatch"]
            ):
                return ir
        except LiveMatrixError as exc:
            last_error = exc
        time.sleep(2.0)
    raise LiveMatrixError("WA-IR did not remain safely unavailable during FI host loss") from last_error


def _cleanup_fi_host_loss(args: Any, plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    values = _fi_host_loss_state(state)
    power_on = None
    if values["phase"] == "fi_powered_off":
        try:
            power_on = _destructive_power(
                args,
                plan,
                scenario_id="fi_host_loss_without_national_cutoff",
                role="webapp_fi",
                action="power-on",
            )
        except LiveMatrixError:
            _destructive_active_provider_intent(
                args,
                plan,
                scenario_id="fi_host_loss_without_national_cutoff",
                role="webapp_fi",
            )
    pair = _wait_for_witness_recovery(
        plan,
        writer_epoch=int(values["writer_epoch"]),
        timeout_seconds=900.0,
    )
    _state_file(plan).unlink()
    return {"power_on": power_on, "writer_pair": pair}


def _run_fi_host_loss(args: Any, plan: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    if _state_file(plan).exists():
        raise LiveMatrixError("FI host-loss test cannot start with retained active state")
    before = _writer_pair_observation(plan)
    epoch = int(before["webapp_fi"]["writer_epoch"])
    _assert_writer_pair(before, active_site="webapp_fi", writer_epoch=epoch)
    ingress_before = _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    state = {
        "schema": ACTIVE_FAULT_SCHEMA,
        "kind": "destructive_fi_host_loss",
        "operation_id": args.operation_id,
        "scenario_id": "fi_host_loss_without_national_cutoff",
        "phase": "prepared",
        "writer_epoch": epoch,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _fi_host_loss_state(state)
    _write_state(plan, state)
    power_off = _destructive_power(
        args,
        plan,
        scenario_id="fi_host_loss_without_national_cutoff",
        role="webapp_fi",
        action="power-off",
    )
    state["phase"] = "fi_powered_off"
    _write_state(plan, state)
    ir_safe = _wait_for_ir_safe_unavailable(plan, writer_epoch=epoch, timeout_seconds=300.0)
    power_on = _destructive_power(
        args,
        plan,
        scenario_id="fi_host_loss_without_national_cutoff",
        role="webapp_fi",
        action="power-on",
    )
    state["phase"] = "fi_powered_on"
    _write_state(plan, state)
    recovered = _wait_for_witness_recovery(plan, writer_epoch=epoch, timeout_seconds=900.0)
    ingress_after = _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    _state_file(plan).unlink()
    outcome = {
        "fi_vm_loss_did_not_promote_ir_without_national_cutoff": True,
        "wa_ir_explicitly_remained_fenced_safe_unavailable": True,
        "exact_fi_writer_epoch_recovered_after_power_on": True,
        "public_ingress_returned_only_after_fi_writer_recovery": True,
        "campaign_bound_fi_power_audit_completed": True,
    }
    return outcome, {
        "writer_before": before,
        "ingress_before": ingress_before,
        "power_off": power_off,
        "ir_safe_unavailable": ir_safe,
        "power_on": power_on,
        "writer_recovered": recovered,
        "ingress_after": ingress_after,
    }


def _verify_fi_host_loss(args: Any, plan: dict[str, Any], runner: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    expected = runner.get("expected_outcome")
    retained = runner.get("doer_observations")
    if not isinstance(expected, dict) or not isinstance(retained, dict):
        raise LiveMatrixError("FI host-loss retained evidence is incomplete")
    before = retained.get("writer_before")
    if not isinstance(before, dict) or type(before.get("webapp_fi", {}).get("writer_epoch")) is not int:
        raise LiveMatrixError("FI host-loss prior Writer evidence is invalid")
    epoch = int(before["webapp_fi"]["writer_epoch"])
    pair = _wait_for_witness_recovery(plan, writer_epoch=epoch, timeout_seconds=300.0)
    ingress = _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    provider = _destructive_active_provider_intent(
        args,
        plan,
        scenario_id="fi_host_loss_without_national_cutoff",
        role="webapp_fi",
    )
    observed = {
        "fi_vm_loss_did_not_promote_ir_without_national_cutoff": True,
        "wa_ir_explicitly_remained_fenced_safe_unavailable": True,
        "exact_fi_writer_epoch_recovered_after_power_on": True,
        "public_ingress_returned_only_after_fi_writer_recovery": True,
        "campaign_bound_fi_power_audit_completed": True,
    }
    if observed != expected:
        raise LiveMatrixError("FI host-loss oracle differs from doer")
    return observed, {
        "fresh_writer_pair": pair,
        "fresh_ingress": ingress,
        "fresh_provider_intent_sha256": provider["intent_sha256"],
    }


def _ir_active_origin_loss_state(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema", "kind", "operation_id", "scenario_id", "phase",
        "writer_epoch", "created_at",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != ACTIVE_FAULT_SCHEMA
        or value.get("kind") != "destructive_ir_active_origin_loss"
        or value.get("scenario_id") != "ir_only_active_origin_loss_is_safe_unavailable"
        or re.fullmatch(r"[0-9a-f-]{36}", str(value.get("operation_id") or "")) is None
        or value.get("phase") not in {"prepared", "ir_powered_off", "ir_powered_on"}
        or type(value.get("writer_epoch")) is not int
        or int(value["writer_epoch"]) < 2
    ):
        raise LiveMatrixError("destructive IR active-origin-loss state is invalid")
    _utc_snapshot(value.get("created_at"), label="destructive IR active-origin-loss creation")
    return dict(value)


def _wait_for_fi_safe_unavailable(
    plan: dict[str, Any],
    *,
    writer_epoch: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Observe FI only while the IR Writer VM is intentionally unavailable."""

    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            fi = _writer_lease_observation(plan, "webapp_fi")
            if (
                fi.get("active_site") == "webapp_ir"
                and fi.get("writer_epoch") == writer_epoch
                and fi.get("control_state") == "active"
                and fi.get("local_active_with_witness_lease") is False
                and fi.get("local_active_reasons") == ["writer_active_site_mismatch"]
            ):
                return fi
        except LiveMatrixError as exc:
            last_error = exc
        time.sleep(2.0)
    raise LiveMatrixError("WA-FI did not remain safely unavailable during IR active-origin loss") from last_error


def _public_ingress_safe_unavailable(plan: dict[str, Any]) -> dict[str, Any]:
    from scripts.full_matrix_live.public_ingress_probe import (
        PublicIngressProbeError,
        probe_safe_unavailable,
    )

    ingress = plan["_ingress"]
    try:
        value = probe_safe_unavailable(
            release_sha=str(plan["release_sha"]),
            client_auth_file=Path(str(ingress["client_auth_file"])),
            client_auth_sha256=str(ingress["client_auth_sha256"]),
        )
    except PublicIngressProbeError as exc:
        raise LiveMatrixError("public ingress did not prove safe unavailability") from exc
    fields = {
        "schema", "status", "public_host", "release_sha", "first_http_status",
        "second_http_status", "first_response_sha256", "second_response_sha256",
        "tls_authenticated_uncached_fail_closed",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != PUBLIC_INGRESS_PROBE_SCHEMA
        or value.get("status") != "safe_unavailable"
        or value.get("public_host") != "app.gold-trading.ir"
        or value.get("release_sha") != plan["release_sha"]
        or value.get("first_http_status") not in {502, 503, 504}
        or value.get("second_http_status") not in {502, 503, 504}
        or value.get("tls_authenticated_uncached_fail_closed") is not True
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value.get(name) or "")) is None
            for name in ("first_response_sha256", "second_response_sha256")
        )
    ):
        raise LiveMatrixError("public safe-unavailable evidence is invalid")
    return value


def _wait_for_ir_writer_recovery(
    plan: dict[str, Any],
    *,
    writer_epoch: int,
    timeout_seconds: float,
) -> dict[str, dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            pair = _writer_pair_observation(plan)
            _assert_writer_pair(pair, active_site="webapp_ir", writer_epoch=writer_epoch)
            return pair
        except LiveMatrixError as exc:
            last_error = exc
        time.sleep(2.0)
    raise LiveMatrixError("IR Writer did not recover to its exact fenced epoch") from last_error


def _cleanup_ir_active_origin_loss(args: Any, plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    values = _ir_active_origin_loss_state(state)
    power_on = None
    if values["phase"] == "ir_powered_off":
        try:
            power_on = _destructive_power(
                args, plan,
                scenario_id="ir_only_active_origin_loss_is_safe_unavailable",
                role="webapp_ir", action="power-on",
            )
        except LiveMatrixError:
            _destructive_active_provider_intent(
                args, plan,
                scenario_id="ir_only_active_origin_loss_is_safe_unavailable",
                role="webapp_ir",
            )
    pair = _wait_for_ir_writer_recovery(
        plan, writer_epoch=int(values["writer_epoch"]), timeout_seconds=900.0,
    )
    _state_file(plan).unlink()
    return {"power_on": power_on, "writer_pair": pair}


def _run_ir_active_origin_loss(args: Any, plan: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    if _state_file(plan).exists():
        raise LiveMatrixError("IR active-origin-loss test cannot start with retained active state")
    lifecycle = _writer_lifecycle_state(plan)
    if lifecycle["iteration"] != args.iteration:
        raise LiveMatrixError("IR active-origin-loss lifecycle belongs to another iteration")
    epoch = int(lifecycle["writer_epoch_after"])
    before = _writer_pair_observation(plan)
    _assert_writer_pair(before, active_site="webapp_ir", writer_epoch=epoch)
    ingress_before = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    state = {
        "schema": ACTIVE_FAULT_SCHEMA,
        "kind": "destructive_ir_active_origin_loss",
        "operation_id": args.operation_id,
        "scenario_id": "ir_only_active_origin_loss_is_safe_unavailable",
        "phase": "prepared",
        "writer_epoch": epoch,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _ir_active_origin_loss_state(state)
    _write_state(plan, state)
    power_off = _destructive_power(
        args, plan,
        scenario_id="ir_only_active_origin_loss_is_safe_unavailable",
        role="webapp_ir", action="power-off",
    )
    state["phase"] = "ir_powered_off"
    _write_state(plan, state)
    fi_safe = _wait_for_fi_safe_unavailable(plan, writer_epoch=epoch, timeout_seconds=300.0)
    ingress_safe = _public_ingress_safe_unavailable(plan)
    power_on = _destructive_power(
        args, plan,
        scenario_id="ir_only_active_origin_loss_is_safe_unavailable",
        role="webapp_ir", action="power-on",
    )
    state["phase"] = "ir_powered_on"
    _write_state(plan, state)
    recovered = _wait_for_ir_writer_recovery(plan, writer_epoch=epoch, timeout_seconds=900.0)
    ingress_after = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    _state_file(plan).unlink()
    outcome = {
        "ir_active_origin_loss_never_promoted_fi_without_failback": True,
        "wa_fi_explicitly_remained_fenced_safe_unavailable": True,
        "public_ingress_failed_closed_without_stale_or_substitute_origin": True,
        "exact_ir_writer_epoch_recovered_after_power_on": True,
        "campaign_bound_ir_power_audit_completed": True,
    }
    return outcome, {
        "writer_lifecycle": lifecycle,
        "writer_before": before,
        "ingress_before": ingress_before,
        "power_off": power_off,
        "fi_safe_unavailable": fi_safe,
        "ingress_safe_unavailable": ingress_safe,
        "power_on": power_on,
        "writer_recovered": recovered,
        "ingress_after": ingress_after,
    }


def _verify_ir_active_origin_loss(args: Any, plan: dict[str, Any], runner: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    expected = runner.get("expected_outcome")
    retained = runner.get("doer_observations")
    if not isinstance(expected, dict) or not isinstance(retained, dict):
        raise LiveMatrixError("IR active-origin-loss retained evidence is incomplete")
    lifecycle = _writer_lifecycle_state(plan)
    if lifecycle["iteration"] != args.iteration:
        raise LiveMatrixError("IR active-origin-loss oracle lifecycle differs from iteration")
    epoch = int(lifecycle["writer_epoch_after"])
    pair = _wait_for_ir_writer_recovery(plan, writer_epoch=epoch, timeout_seconds=300.0)
    ingress = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    provider = _destructive_active_provider_intent(
        args, plan,
        scenario_id="ir_only_active_origin_loss_is_safe_unavailable", role="webapp_ir",
    )
    observed = {
        "ir_active_origin_loss_never_promoted_fi_without_failback": True,
        "wa_fi_explicitly_remained_fenced_safe_unavailable": True,
        "public_ingress_failed_closed_without_stale_or_substitute_origin": True,
        "exact_ir_writer_epoch_recovered_after_power_on": True,
        "campaign_bound_ir_power_audit_completed": True,
    }
    if observed != expected:
        raise LiveMatrixError("IR active-origin-loss oracle differs from doer")
    return observed, {
        "fresh_writer_pair": pair,
        "fresh_ingress": ingress,
        "fresh_provider_intent_sha256": provider["intent_sha256"],
        "writer_lifecycle_sha256": hash_summary(lifecycle),
    }


def _fi_recovery_hub_loss_state(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema", "kind", "operation_id", "scenario_id", "phase",
        "writer_epoch", "created_at",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != ACTIVE_FAULT_SCHEMA
        or value.get("kind") != "destructive_fi_recovery_hub_loss"
        or value.get("scenario_id") != "permanent_fi_recovery_hub_loss"
        or re.fullmatch(r"[0-9a-f-]{36}", str(value.get("operation_id") or "")) is None
        or value.get("phase") not in {"prepared", "fi_powered_off", "fi_powered_on"}
        or type(value.get("writer_epoch")) is not int
        or int(value["writer_epoch"]) < 2
    ):
        raise LiveMatrixError("destructive FI recovery-hub-loss state is invalid")
    _utc_snapshot(value.get("created_at"), label="destructive FI recovery-hub-loss creation")
    return dict(value)


def _wait_for_ir_active_without_fi_hub(
    plan: dict[str, Any],
    *,
    writer_epoch: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Observe only WA-IR while the Finland relay hub is powered down."""

    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            ir = _writer_lease_observation(plan, "webapp_ir")
            if (
                ir.get("active_site") == "webapp_ir"
                and ir.get("writer_epoch") == writer_epoch
                and ir.get("control_state") == "active"
                and ir.get("local_active_with_witness_lease") is True
                and ir.get("local_active_reasons") == []
            ):
                return ir
        except LiveMatrixError as exc:
            last_error = exc
        time.sleep(2.0)
    raise LiveMatrixError("WA-IR did not retain the only active Writer while FI recovery hub was lost") from last_error


def _cleanup_fi_recovery_hub_loss(args: Any, plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    values = _fi_recovery_hub_loss_state(state)
    power_on = None
    if values["phase"] == "fi_powered_off":
        try:
            power_on = _destructive_power(
                args, plan,
                scenario_id="permanent_fi_recovery_hub_loss",
                role="webapp_fi", action="power-on",
            )
        except LiveMatrixError:
            _destructive_active_provider_intent(
                args, plan,
                scenario_id="permanent_fi_recovery_hub_loss", role="webapp_fi",
            )
    pair = _wait_for_ir_writer_recovery(
        plan, writer_epoch=int(values["writer_epoch"]), timeout_seconds=900.0,
    )
    convergence, states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    _state_file(plan).unlink()
    return {
        "power_on": power_on,
        "writer_pair": pair,
        "convergence": convergence,
        "convergence_states_sha256": hash_summary(states),
    }


def _run_fi_recovery_hub_loss(args: Any, plan: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    """Exercise FI relay-hub loss while an existing IR Writer remains legal.

    A bounded power loss is the safe destructive representation of a permanent
    hub loss in a single immutable campaign: it proves that no new Bot-FI to
    WA-IR path, FI promotion, or public-origin substitution is used while the
    relay host is absent.  Rebuild/replacement is deliberately a separately
    re-attested post-campaign operation; the handler never destroys a volume
    or mutates inventory identity.
    """

    if _state_file(plan).exists():
        raise LiveMatrixError("FI recovery-hub-loss test cannot start with retained active state")
    lifecycle = _writer_lifecycle_state(plan)
    if lifecycle["iteration"] != args.iteration:
        raise LiveMatrixError("FI recovery-hub-loss lifecycle belongs to another iteration")
    epoch = int(lifecycle["writer_epoch_after"])
    before = _writer_pair_observation(plan)
    _assert_writer_pair(before, active_site="webapp_ir", writer_epoch=epoch)
    ingress_before = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    state = {
        "schema": ACTIVE_FAULT_SCHEMA,
        "kind": "destructive_fi_recovery_hub_loss",
        "operation_id": args.operation_id,
        "scenario_id": "permanent_fi_recovery_hub_loss",
        "phase": "prepared",
        "writer_epoch": epoch,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _fi_recovery_hub_loss_state(state)
    _write_state(plan, state)
    power_off = _destructive_power(
        args, plan,
        scenario_id="permanent_fi_recovery_hub_loss",
        role="webapp_fi", action="power-off",
    )
    state["phase"] = "fi_powered_off"
    _write_state(plan, state)
    ir_active = _wait_for_ir_active_without_fi_hub(
        plan, writer_epoch=epoch, timeout_seconds=300.0,
    )
    ingress_during_loss = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    power_on = _destructive_power(
        args, plan,
        scenario_id="permanent_fi_recovery_hub_loss",
        role="webapp_fi", action="power-on",
    )
    state["phase"] = "fi_powered_on"
    _write_state(plan, state)
    recovered = _wait_for_ir_writer_recovery(plan, writer_epoch=epoch, timeout_seconds=900.0)
    convergence, states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    ingress_after = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    _state_file(plan).unlink()
    outcome = {
        "fi_recovery_hub_loss_did_not_change_ir_writer_epoch": True,
        "wa_ir_remained_the_only_witness_leased_writer": True,
        "public_ingress_remained_on_ir_without_fi_substitution": True,
        "fi_hub_rejoin_converged_without_direct_bot_to_ir_path": True,
        "campaign_bound_fi_hub_power_audit_completed": True,
    }
    return outcome, {
        "writer_lifecycle": lifecycle,
        "writer_before": before,
        "ingress_before": ingress_before,
        "power_off": power_off,
        "ir_active_while_hub_lost": ir_active,
        "ingress_during_hub_loss": ingress_during_loss,
        "power_on": power_on,
        "writer_recovered": recovered,
        "convergence": convergence,
        "convergence_states_sha256": hash_summary(states),
        "ingress_after": ingress_after,
    }


def _verify_fi_recovery_hub_loss(args: Any, plan: dict[str, Any], runner: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    expected = runner.get("expected_outcome")
    retained = runner.get("doer_observations")
    if not isinstance(expected, dict) or not isinstance(retained, dict):
        raise LiveMatrixError("FI recovery-hub-loss retained evidence is incomplete")
    lifecycle = _writer_lifecycle_state(plan)
    if lifecycle["iteration"] != args.iteration:
        raise LiveMatrixError("FI recovery-hub-loss oracle lifecycle differs from iteration")
    epoch = int(lifecycle["writer_epoch_after"])
    pair = _wait_for_ir_writer_recovery(plan, writer_epoch=epoch, timeout_seconds=300.0)
    convergence, states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    ingress = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    provider = _destructive_active_provider_intent(
        args, plan, scenario_id="permanent_fi_recovery_hub_loss", role="webapp_fi",
    )
    observed = {
        "fi_recovery_hub_loss_did_not_change_ir_writer_epoch": True,
        "wa_ir_remained_the_only_witness_leased_writer": True,
        "public_ingress_remained_on_ir_without_fi_substitution": True,
        "fi_hub_rejoin_converged_without_direct_bot_to_ir_path": True,
        "campaign_bound_fi_hub_power_audit_completed": True,
    }
    if observed != expected:
        raise LiveMatrixError("FI recovery-hub-loss oracle differs from doer")
    return observed, {
        "fresh_writer_pair": pair,
        "fresh_convergence": convergence,
        "fresh_convergence_states_sha256": hash_summary(states),
        "fresh_ingress": ingress,
        "fresh_provider_intent_sha256": provider["intent_sha256"],
        "writer_lifecycle_sha256": hash_summary(lifecycle),
    }


def _power_loss_cutpoint_state(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema", "kind", "operation_id", "scenario_id", "phase",
        "writer_epoch_before", "writer_epoch_after", "transition_operation_id",
        "plan_hash", "iteration", "created_at",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != ACTIVE_FAULT_SCHEMA
        or value.get("kind") != "destructive_power_loss_cutpoint"
        or value.get("scenario_id") != "power_loss_between_fence_and_enable"
        or re.fullmatch(r"[0-9a-f-]{36}", str(value.get("operation_id") or "")) is None
        or value.get("phase") not in {"prepared", "paused", "fi_powered_off", "ir_enabled", "fi_powered_on"}
        or type(value.get("writer_epoch_before")) is not int
        or type(value.get("writer_epoch_after")) is not int
        or int(value["writer_epoch_before"]) < 1
        or int(value["writer_epoch_after"]) != int(value["writer_epoch_before"]) + 1
        or value.get("iteration") not in {1, 2}
    ):
        raise LiveMatrixError("destructive power-loss cutpoint state is invalid")
    if value["phase"] == "prepared":
        if value.get("transition_operation_id") is not None or value.get("plan_hash") is not None:
            raise LiveMatrixError("unstarted power-loss state retains JIT identity")
    elif (
        re.fullmatch(r"[0-9a-f-]{36}", str(value.get("transition_operation_id") or "")) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(value.get("plan_hash") or "")) is None
    ):
        raise LiveMatrixError("power-loss cutpoint state lacks exact JIT identity")
    _utc_snapshot(value.get("created_at"), label="destructive power-loss cutpoint creation")
    return dict(value)


def _assert_power_transition(
    transition: dict[str, Any],
    *,
    status: str,
    epoch_before: int,
) -> None:
    if (
        not isinstance(transition, dict)
        or transition.get("status") != status
        or transition.get("source_site") != "webapp_fi"
        or transition.get("target_site") != "webapp_ir"
        or transition.get("writer_epoch_before") != epoch_before
        or transition.get("writer_epoch_after") != epoch_before + 1
        or transition.get("connectivity_mode") != "isolated"
        or int(transition.get("connectivity_consecutive_rounds") or 0) < 3
        or re.fullmatch(r"[0-9a-f-]{36}", str(transition.get("operation_id") or "")) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(transition.get("plan_hash") or "")) is None
    ):
        raise LiveMatrixError("power-loss transition did not retain its exact isolated JIT plan")
    if status == "paused" and transition.get("paused_after_step") != "source_connections_drained":
        raise LiveMatrixError("power-loss transition paused outside the source-drain cutpoint")


def _record_power_loss_lifecycle(args: Any, plan: dict[str, Any], state: dict[str, Any]) -> None:
    """Persist the IR-active state once, so the ordinary cutoff step resumes it."""

    path = _writer_lifecycle_path(plan)
    if path.exists() or path.is_symlink():
        lifecycle = _writer_lifecycle_state(plan)
        if (
            lifecycle["iteration"] != args.iteration
            or lifecycle["promotion_operation_id"] != state["transition_operation_id"]
            or lifecycle["promotion_plan_hash"] != state["plan_hash"]
            or lifecycle["writer_epoch_before"] != state["writer_epoch_before"]
            or lifecycle["writer_epoch_after"] != state["writer_epoch_after"]
        ):
            raise LiveMatrixError("power-loss lifecycle checkpoint differs from the completed JIT promotion")
        return
    _write_writer_lifecycle(
        plan,
        {
            "schema": WRITER_LIFECYCLE_SCHEMA,
            "campaign_id": plan["campaign_id"],
            "release_sha": plan["release_sha"],
            "iteration": args.iteration,
            "phase": "ir_active",
            "promotion_operation_id": state["transition_operation_id"],
            "promotion_plan_hash": state["plan_hash"],
            "writer_epoch_before": state["writer_epoch_before"],
            "writer_epoch_after": state["writer_epoch_after"],
            "connectivity_mode": "isolated",
            "connectivity_consecutive_rounds": 3,
        },
    )


def _power_on_fi_after_cutpoint(args: Any, plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    values = _power_loss_cutpoint_state(state)
    if values["phase"] not in {"fi_powered_off", "ir_enabled"}:
        return None
    try:
        result = _destructive_power(
            args, plan,
            scenario_id="power_loss_between_fence_and_enable",
            role="webapp_fi", action="power-on",
        )
    except LiveMatrixError:
        _destructive_active_provider_intent(
            args, plan,
            scenario_id="power_loss_between_fence_and_enable", role="webapp_fi",
        )
        result = None
    state["phase"] = "fi_powered_on"
    _write_state(plan, state)
    return result


def _cleanup_power_loss_cutpoint(args: Any, plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    values = _power_loss_cutpoint_state(state)
    if values["phase"] == "prepared":
        # No provider action is permitted from an unstarted controller record.
        # If the JIT invocation had produced any partial artifact, its own
        # strict recovery gate will retain it rather than guessing a Writer.
        _state_file(plan).unlink()
        return {"status": "unstarted_state_removed"}
    if values["phase"] in {"paused", "fi_powered_off"}:
        resumed = execute_transition(
            plan,
            scenario_id="power_loss_between_fence_and_enable",
            iteration=args.iteration,
            action="promote_ir",
        )
        _assert_power_transition(resumed, status="completed", epoch_before=int(values["writer_epoch_before"]))
        state["phase"] = "ir_enabled"
        _write_state(plan, state)
    pair = _wait_for_ir_writer_recovery(
        plan, writer_epoch=int(values["writer_epoch_after"]), timeout_seconds=900.0,
    )
    _record_power_loss_lifecycle(args, plan, state)
    power_on = _power_on_fi_after_cutpoint(args, plan, state)
    recovered = _wait_for_ir_writer_recovery(
        plan, writer_epoch=int(values["writer_epoch_after"]), timeout_seconds=900.0,
    )
    _state_file(plan).unlink()
    return {"power_on": power_on, "ir_pair_before_fi_rejoin": pair, "ir_pair_after_fi_rejoin": recovered}


def _run_power_loss_cutpoint(args: Any, plan: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    if _state_file(plan).exists() or _writer_lifecycle_path(plan).exists():
        raise LiveMatrixError("power-loss cutpoint requires a fresh FI Writer lifecycle")
    before = _writer_pair_observation(plan)
    before_epoch = int(before["webapp_fi"]["writer_epoch"])
    _assert_writer_pair(before, active_site="webapp_fi", writer_epoch=before_epoch)
    ingress_before = _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    state = {
        "schema": ACTIVE_FAULT_SCHEMA,
        "kind": "destructive_power_loss_cutpoint",
        "operation_id": args.operation_id,
        "scenario_id": "power_loss_between_fence_and_enable",
        "phase": "prepared",
        "writer_epoch_before": before_epoch,
        "writer_epoch_after": before_epoch + 1,
        "transition_operation_id": None,
        "plan_hash": None,
        "iteration": args.iteration,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _power_loss_cutpoint_state(state)
    _write_state(plan, state)
    paused = execute_transition(
        plan,
        scenario_id="power_loss_between_fence_and_enable",
        iteration=args.iteration,
        action="promote_ir",
        pause_after_source_drain_for_power_loss=True,
    )
    _assert_power_transition(paused, status="paused", epoch_before=before_epoch)
    state.update(
        {
            "phase": "paused",
            "transition_operation_id": paused["operation_id"],
            "plan_hash": paused["plan_hash"],
        }
    )
    _write_state(plan, state)
    power_off = _destructive_power(
        args, plan,
        scenario_id="power_loss_between_fence_and_enable", role="webapp_fi", action="power-off",
    )
    state["phase"] = "fi_powered_off"
    _write_state(plan, state)
    completed = execute_transition(
        plan,
        scenario_id="power_loss_between_fence_and_enable",
        iteration=args.iteration,
        action="promote_ir",
    )
    _assert_power_transition(completed, status="completed", epoch_before=before_epoch)
    ir_pair = _wait_for_ir_writer_recovery(plan, writer_epoch=before_epoch + 1, timeout_seconds=900.0)
    state["phase"] = "ir_enabled"
    _write_state(plan, state)
    _record_power_loss_lifecycle(args, plan, state)
    ingress_ir = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    power_on = _power_on_fi_after_cutpoint(args, plan, state)
    recovered = _wait_for_ir_writer_recovery(plan, writer_epoch=before_epoch + 1, timeout_seconds=900.0)
    ingress_after = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    _state_file(plan).unlink()
    outcome = {
        "source_fence_and_connection_drain_were_durable_before_power_loss": True,
        "only_campaign_bound_fi_power_off_occurred_before_ir_target_enable": True,
        "same_jit_plan_resumed_through_ir_pull_only_target_enable": True,
        "ir_became_only_witness_writer_at_the_exact_next_epoch": True,
        "fi_rejoined_as_nonwriter_without_direct_fi_to_ir_control_path": True,
    }
    return outcome, {
        "writer_before": before,
        "ingress_before": ingress_before,
        "paused_transition": paused,
        "power_off": power_off,
        "completed_transition": completed,
        "ir_writer_before_fi_rejoin": ir_pair,
        "ingress_ir": ingress_ir,
        "power_on": power_on,
        "ir_writer_after_fi_rejoin": recovered,
        "ingress_after": ingress_after,
    }


def _verify_power_loss_cutpoint(args: Any, plan: dict[str, Any], runner: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    expected = runner.get("expected_outcome")
    retained = runner.get("doer_observations")
    if not isinstance(expected, dict) or not isinstance(retained, dict):
        raise LiveMatrixError("power-loss cutpoint retained evidence is incomplete")
    paused = retained.get("paused_transition")
    completed = retained.get("completed_transition")
    if not isinstance(paused, dict) or not isinstance(completed, dict):
        raise LiveMatrixError("power-loss cutpoint transition evidence is incomplete")
    before = retained.get("writer_before")
    if not isinstance(before, dict) or type(before.get("webapp_fi", {}).get("writer_epoch")) is not int:
        raise LiveMatrixError("power-loss cutpoint prior Writer evidence is invalid")
    epoch = int(before["webapp_fi"]["writer_epoch"])
    _assert_power_transition(paused, status="paused", epoch_before=epoch)
    _assert_power_transition(completed, status="completed", epoch_before=epoch)
    lifecycle = _writer_lifecycle_state(plan)
    if lifecycle["promotion_operation_id"] != completed["operation_id"]:
        raise LiveMatrixError("power-loss lifecycle does not retain resumed operation identity")
    pair = _wait_for_ir_writer_recovery(plan, writer_epoch=epoch + 1, timeout_seconds=300.0)
    ingress = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    provider = _destructive_active_provider_intent(
        args, plan, scenario_id="power_loss_between_fence_and_enable", role="webapp_fi",
    )
    observed = {
        "source_fence_and_connection_drain_were_durable_before_power_loss": True,
        "only_campaign_bound_fi_power_off_occurred_before_ir_target_enable": True,
        "same_jit_plan_resumed_through_ir_pull_only_target_enable": True,
        "ir_became_only_witness_writer_at_the_exact_next_epoch": True,
        "fi_rejoined_as_nonwriter_without_direct_fi_to_ir_control_path": True,
    }
    if observed != expected:
        raise LiveMatrixError("power-loss cutpoint oracle differs from doer")
    return observed, {
        "fresh_ir_writer_pair": pair,
        "fresh_ingress": ingress,
        "fresh_provider_intent_sha256": provider["intent_sha256"],
        "writer_lifecycle_sha256": hash_summary(lifecycle),
    }


_CAPACITY_AGENT_PATH = "/srv/trading-bot-three-site/current/scripts/full_matrix_live/capacity_fault_agent.py"
_CAPACITY_PROBE_SCHEMA = "three-site-full-matrix-capacity-writer-fence-probe-v1"


def _ensure_fi_writer_before_capacity(args: Any, plan: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any] | None]:
    """Return to normal Writer ownership before the FI storage drill.

    A dedicated campaign reaches this point after the real power-cut promotion
    and IR-active destructive cases.  It consumes the capacity scenario's
    separately schedule-bound IR→FI failback; a shared-safe execution arrives
    here already FI-active and performs no transition.
    """

    pair = _writer_pair_observation(plan)
    active = pair["webapp_fi"].get("active_site")
    epoch = pair["webapp_fi"].get("writer_epoch")
    if active == "webapp_fi" and type(epoch) is int:
        _assert_writer_pair(pair, active_site="webapp_fi", writer_epoch=int(epoch))
        return pair, None
    if active != "webapp_ir" or type(epoch) is not int:
        raise LiveMatrixError("capacity drill has no coherent WebApp Writer before failback")
    _assert_writer_pair(pair, active_site="webapp_ir", writer_epoch=int(epoch))
    lifecycle = _writer_lifecycle_state(plan)
    if lifecycle["iteration"] != args.iteration or lifecycle["writer_epoch_after"] != epoch:
        raise LiveMatrixError("capacity drill IR lifecycle does not belong to this iteration")
    transition = execute_transition(
        plan,
        scenario_id="wal_event_redis_blob_capacity_exhaustion_safe",
        iteration=args.iteration,
        action="failback_fi",
    )
    if (
        transition.get("status") != "completed"
        or transition.get("source_site") != "webapp_ir"
        or transition.get("target_site") != "webapp_fi"
        or transition.get("writer_epoch_before") != epoch
        or transition.get("writer_epoch_after") != epoch + 1
        or transition.get("connectivity_mode") != "online"
        or int(transition.get("connectivity_consecutive_rounds") or 0) < 3
    ):
        raise LiveMatrixError("capacity drill FI failback did not complete on restored connectivity")
    after = _writer_pair_observation(plan)
    _assert_writer_pair(after, active_site="webapp_fi", writer_epoch=epoch + 1)
    _remove_writer_lifecycle(plan)
    _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    return after, transition


def _capacity_fault_state(value: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema", "kind", "operation_id", "scenario_id", "phase",
        "writer_epoch", "created_at",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != ACTIVE_FAULT_SCHEMA
        or value.get("kind") != "destructive_webapp_fi_capacity_fault"
        or value.get("scenario_id") != "wal_event_redis_blob_capacity_exhaustion_safe"
        or re.fullmatch(r"[0-9a-f-]{36}", str(value.get("operation_id") or "")) is None
        or value.get("phase") not in {"prepared", "armed", "released"}
        or type(value.get("writer_epoch")) is not int
        or int(value["writer_epoch"]) < 1
    ):
        raise LiveMatrixError("destructive capacity-fault state is invalid")
    _utc_snapshot(value.get("created_at"), label="destructive capacity-fault creation")
    return dict(value)


def _capacity_agent_operation(
    args: Any,
    plan: dict[str, Any],
    *,
    action: str,
) -> dict[str, Any]:
    """Invoke only the release-owned, fixed WA-FI capacity actuator."""

    if action not in {"arm", "status", "disarm"}:
        raise LiveMatrixError("capacity actuator action is outside its closed surface")
    role = plan["_roles"]["webapp_fi"]
    if role.get("transport") != "ssh":
        raise LiveMatrixError("capacity actuator requires the pinned WebApp-FI SSH transport")
    command = [
        "/usr/bin/python3", _CAPACITY_AGENT_PATH, action,
        "--campaign-id", str(args.campaign_id),
        "--release-sha", str(args.release_sha),
        "--operation-id", str(args.operation_id),
    ]
    if action in {"arm", "disarm"}:
        command.extend(
            [
                "--apply",
                "--confirm",
                f"capacity-fault:{args.operation_id}:webapp_fi:{action}:{args.release_sha}",
            ]
        )
    result = run_role_command(
        "webapp_fi", role, command, timeout=900, allow_stderr=True,
    )
    try:
        payload = json.loads(result["stdout"], object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError("capacity actuator returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("operation_id") != args.operation_id:
        raise LiveMatrixError("capacity actuator response identity differs")
    status = payload.get("status")
    if status == "clear":
        if set(payload) != {"status", "operation_id"}:
            raise LiveMatrixError("capacity actuator clear response is malformed")
        return payload
    if status not in {"armed", "cleared"} or set(payload) - {
        "status", "operation_id", "storage_total_bytes", "available_bytes",
        "hard_limit_bytes", "marker_sha256",
    }:
        raise LiveMatrixError("capacity actuator response is malformed")
    required = {"status", "operation_id", "storage_total_bytes", "available_bytes", "hard_limit_bytes"}
    if not required <= set(payload) or any(
        type(payload.get(name)) is not int or int(payload[name]) <= 0
        for name in ("storage_total_bytes", "available_bytes", "hard_limit_bytes")
    ) or int(payload["hard_limit_bytes"]) >= int(payload["storage_total_bytes"]):
        raise LiveMatrixError("capacity actuator numeric evidence is invalid")
    if status == "armed":
        if (
            set(payload) != required | {"marker_sha256"}
            or int(payload["available_bytes"]) > int(payload["hard_limit_bytes"])
            or re.fullmatch(r"[0-9a-f]{64}", str(payload.get("marker_sha256") or "")) is None
        ):
            raise LiveMatrixError("capacity actuator did not prove its hard watermark")
    elif set(payload) != required or int(payload["available_bytes"]) <= int(payload["hard_limit_bytes"]):
        raise LiveMatrixError("capacity actuator did not prove restored headroom")
    return payload


def _capacity_writer_fence_probe(plan: dict[str, Any]) -> dict[str, Any]:
    role = plan["_roles"]["webapp_fi"]
    result = run_compose_role_service(
        "webapp_fi", role,
        service=ROLE_WORKLOAD_SERVICE["webapp_fi"],
        command=[
            "/app/scripts/full_matrix_live/capacity_writer_fence_probe.py",
            "--expected-reason", "full_matrix_capacity_hard_limit",
        ],
        timeout=180,
    )
    try:
        payload = json.loads(result["stdout"], object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError("capacity writer-fence probe output is invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "status", "http_status", "reason", "response_sha256"}
        or payload.get("schema") != _CAPACITY_PROBE_SCHEMA
        or payload.get("status") != "passed"
        or payload.get("http_status") != 503
        or payload.get("reason") != "full_matrix_capacity_hard_limit"
        or re.fullmatch(r"[0-9a-f]{64}", str(payload.get("response_sha256") or "")) is None
    ):
        raise LiveMatrixError("capacity writer-fence probe did not prove controlled rejection")
    return payload


def _cleanup_capacity_fault(args: Any, plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    values = _capacity_fault_state(state)
    release = _capacity_agent_operation(args, plan, action="disarm")
    epoch = int(values["writer_epoch"])
    pair = _wait_for_witness_recovery(plan, writer_epoch=epoch, timeout_seconds=900.0)
    _state_file(plan).unlink()
    return {"release": release, "writer_pair": pair}


def _run_capacity_fault(args: Any, plan: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    if _state_file(plan).exists():
        raise LiveMatrixError("capacity fault cannot start with retained active state")
    before, preceding_failback = _ensure_fi_writer_before_capacity(args, plan)
    epoch = int(before["webapp_fi"]["writer_epoch"])
    _assert_writer_pair(before, active_site="webapp_fi", writer_epoch=epoch)
    ingress_before = _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    state = {
        "schema": ACTIVE_FAULT_SCHEMA,
        "kind": "destructive_webapp_fi_capacity_fault",
        "operation_id": args.operation_id,
        "scenario_id": "wal_event_redis_blob_capacity_exhaustion_safe",
        "phase": "prepared",
        "writer_epoch": epoch,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _capacity_fault_state(state)
    _write_state(plan, state)
    armed = _capacity_agent_operation(args, plan, action="arm")
    state["phase"] = "armed"
    _write_state(plan, state)
    fence_probe = _capacity_writer_fence_probe(plan)
    released = _capacity_agent_operation(args, plan, action="disarm")
    state["phase"] = "released"
    _write_state(plan, state)
    pair = _wait_for_witness_recovery(plan, writer_epoch=epoch, timeout_seconds=900.0)
    convergence, states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    ingress_after = _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    snapshots = collect_all_host_snapshots(plan, args.release_sha)
    _state_file(plan).unlink()
    outcome = {
        "dedicated_storage_hard_watermark_was_actually_armed": True,
        "webapp_writer_rejected_unsafe_http_mutation_before_data_plane_write": True,
        "wal_event_redis_and_blob_mount_share_the_guarded_dedicated_storage": True,
        "reserve_release_restored_headroom_before_writer_reopened": True,
        "writer_epoch_convergence_and_public_ingress_remained_exact": True,
    }
    return outcome, {
        "preceding_failback": preceding_failback,
        "writer_before": before,
        "ingress_before": ingress_before,
        "armed": armed,
        "writer_fence_probe": fence_probe,
        "released": released,
        "writer_after": pair,
        "convergence": convergence,
        "convergence_states_sha256": hash_summary(states),
        "host_snapshots_sha256": hash_summary(snapshots),
        "ingress_after": ingress_after,
    }


def _verify_capacity_fault(args: Any, plan: dict[str, Any], runner: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    expected = runner.get("expected_outcome")
    retained = runner.get("doer_observations")
    if not isinstance(expected, dict) or not isinstance(retained, dict):
        raise LiveMatrixError("capacity-fault retained evidence is incomplete")
    before = retained.get("writer_before")
    if not isinstance(before, dict) or type(before.get("webapp_fi", {}).get("writer_epoch")) is not int:
        raise LiveMatrixError("capacity-fault prior Writer evidence is invalid")
    epoch = int(before["webapp_fi"]["writer_epoch"])
    clear = _capacity_agent_operation(args, plan, action="status")
    if clear.get("status") != "clear":
        raise LiveMatrixError("capacity-fault oracle found retained reserve or marker")
    pair = _wait_for_witness_recovery(plan, writer_epoch=epoch, timeout_seconds=300.0)
    convergence, states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    ingress = _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    snapshots = collect_all_host_snapshots(plan, args.release_sha)
    observed = {
        "dedicated_storage_hard_watermark_was_actually_armed": True,
        "webapp_writer_rejected_unsafe_http_mutation_before_data_plane_write": True,
        "wal_event_redis_and_blob_mount_share_the_guarded_dedicated_storage": True,
        "reserve_release_restored_headroom_before_writer_reopened": True,
        "writer_epoch_convergence_and_public_ingress_remained_exact": True,
    }
    if observed != expected:
        raise LiveMatrixError("capacity-fault oracle differs from doer")
    return observed, {
        "capacity_agent_status": clear,
        "fresh_writer_pair": pair,
        "fresh_convergence": convergence,
        "fresh_convergence_states_sha256": hash_summary(states),
        "fresh_host_snapshots_sha256": hash_summary(snapshots),
        "fresh_ingress": ingress,
    }


def _run_recovery_timing_cycle(
    args: Any,
    plan: dict[str, Any],
    *,
    label: str,
) -> tuple[dict[str, bool], dict[str, Any]]:
    """Exercise two bounded IR delivery reconnects and one atomic live emit."""

    scenario_id = "reconnect_flap_and_bounded_catchup"
    if label not in {"doer", "oracle"} or _state_file(plan).exists():
        raise LiveMatrixError("recovery timing cycle cannot start with retained state")
    precondition = _recovery_writer_precondition(plan)
    token = args.operation_id.replace("-", "")[:12]
    suffix = "D" if label == "doer" else "O"
    state = {
        "schema": ACTIVE_FAULT_SCHEMA,
        "kind": "recovery_timing_probe",
        "operation_id": args.operation_id,
        "scenario_id": scenario_id,
        "fault_id": f"FMX_{token}_RCV",
        "fixture_first": f"FMX_{token}_R{suffix}1",
        "fixture_second": f"FMX_{token}_R{suffix}2",
        "fixture_live": f"FMX_{token}_R{suffix}L",
        # The probe itself appends a route and index under a 64-character API
        # idempotency ceiling. Keep every emitter prefix at its stricter 24
        # character bound; the one-character batch marker is appended below.
        "correlation_prefix": f"fmxtiming:{token}{suffix.lower()}",
        "phase": "prepared",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _recovery_timing_state(state)
    _write_state(plan, state)
    emitted: dict[str, dict[str, Any]] = {}
    samples_per_route = 10
    target_rps = 10.0
    try:
        _recovery_delivery_fault(plan, action="pause", fault_id=state["fault_id"])
        state["phase"] = "first_paused"
        _write_state(plan, state)
        emitted["first"] = _run_timing_emitter(
            args,
            plan,
            role_name="webapp_ir",
            fixture_prefix=state["fixture_first"],
            correlation_prefix=f"{state['correlation_prefix']}1",
            samples_per_route=samples_per_route,
            target_rps=target_rps,
        )
        first_snapshots = _recovery_snapshot_set(
            plan, correlation_prefix=state["correlation_prefix"]
        )
        first_pending = _pending_backlog_snapshot(
            first_snapshots, correlation_prefix=state["correlation_prefix"]
        )
        _recovery_delivery_fault(plan, action="resume", fault_id=state["fault_id"])
        state["phase"] = "first_resumed"
        _write_state(plan, state)
        _recovery_delivery_fault(plan, action="pause", fault_id=state["fault_id"])
        state["phase"] = "second_paused"
        _write_state(plan, state)
        emitted["second"] = _run_timing_emitter(
            args,
            plan,
            role_name="webapp_ir",
            fixture_prefix=state["fixture_second"],
            correlation_prefix=f"{state['correlation_prefix']}2",
            samples_per_route=samples_per_route,
            target_rps=target_rps,
        )
        second_snapshots = _recovery_snapshot_set(
            plan, correlation_prefix=state["correlation_prefix"]
        )
        second_pending = _pending_backlog_snapshot(
            second_snapshots, correlation_prefix=state["correlation_prefix"]
        )
        emitted["live"] = _recovery_delivery_resume_emit(
            args,
            plan,
            fault_id=state["fault_id"],
            fixture_prefix=state["fixture_live"],
            correlation_prefix=f"{state['correlation_prefix']}l",
            samples_per_route=samples_per_route,
            target_rps=target_rps,
        )
        state["phase"] = "live_emitted"
        _write_state(plan, state)
        deadline = time.monotonic() + 900.0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            final_snapshots = _recovery_snapshot_set(
                plan, correlation_prefix=state["correlation_prefix"]
            )
            try:
                manifest = _recovery_backlog_manifest(
                    scenario_id=scenario_id,
                    correlation_prefix=state["correlation_prefix"],
                    emitted=emitted,
                    first_pending=first_pending,
                    second_pending=second_pending,
                    final_snapshots=final_snapshots,
                )
                manifest = _timing_manifest_with_journal_durations(
                    manifest=manifest,
                    snapshots=final_snapshots,
                )
                artifact = build_timing_evidence(
                    manifest=manifest,
                    snapshots=final_snapshots,
                    scenario_id=scenario_id,
                )
                break
            except (LiveMatrixError, TimingBuildError, SyncTimingEvidenceError) as exc:
                last_error = exc
                time.sleep(2.0)
        else:
            raise LiveMatrixError("recovery timing probes did not converge before timeout") from last_error
        outcome = _recovery_timing_outcome(artifact)
        cleanup = _cleanup_recovery_timing_probe(plan, _read_state(plan))
        return outcome, {
            "writer_precondition": precondition,
            "emitted": emitted,
            "first_pending": first_pending,
            "second_pending": second_pending,
            "manifest": manifest,
            "artifact": artifact,
            "cleanup": cleanup,
        }
    except Exception:
        # The retained owner-only state is intentionally left for the explicit
        # recovery operation; do not conceal a failed pause or cleanup.
        raise


def _recovery_timing_verify(
    args: Any,
    plan: dict[str, Any],
    runner: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    retained = runner.get("doer_observations")
    expected = runner.get("expected_outcome")
    if (
        not isinstance(retained, dict)
        or not isinstance(retained.get("artifact"), dict)
        or not isinstance(expected, dict)
        or _recovery_timing_outcome(retained["artifact"]) != expected
    ):
        raise LiveMatrixError("recovery timing doer evidence is incomplete")
    observed, independent = _run_recovery_timing_cycle(args, plan, label="oracle")
    if observed != expected:
        raise LiveMatrixError("independent recovery timing oracle differs from doer")
    artifact = independent.get("artifact")
    if not isinstance(artifact, dict):
        raise LiveMatrixError("independent recovery timing artifact is missing")
    artifact_raw = (
        json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    artifact_path = args.artifact_root / f"{args.operation_id}-recovery-sync-timing.json"
    if artifact_path.exists() or artifact_path.is_symlink():
        raise LiveMatrixError("recovery timing evidence path already exists")
    write_secure_atomic_bytes(
        artifact_path,
        artifact_raw,
        label="Full Matrix retained recovery timing evidence",
        mode=0o600,
        max_size=32 * 1024 * 1024,
    )
    independent["timing_evidence"] = {
        "path": artifact_path.name,
        "sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "size": len(artifact_raw),
    }
    return observed, independent


def _run_combined_workload_role(
    args: Any,
    plan: dict[str, Any],
    *,
    role: str,
    scenario_ids: tuple[str, ...],
    attempts_per_scenario: int,
    target_rps: float,
) -> dict[str, Any]:
    prefix = (
        "FMX_"
        + args.operation_id.replace("-", "")[:20]
        + ("_BOT_" if role == "bot_fi" else "_WFI_")
    )
    command = [
        "/app/scripts/run_bot_webapp_comprehensive_load_matrix.py",
        "--prefix",
        prefix,
        "--user-count",
        "96",
        "--attempts-per-scenario",
        str(attempts_per_scenario),
        "--target-rps",
        str(target_rps),
        "--telegram-ratio",
        "0.5",
        "--write-max-concurrency",
        "24",
        "--read-view-max-concurrency",
        "48",
        "--check",
    ]
    for scenario_id in scenario_ids:
        command.extend(["--scenario", scenario_id])
    if role == "webapp_fi":
        command.append("--three-site-writer-fence")
    controller_started = time.monotonic()
    result = run_compose_role_service(
        role,
        plan["_roles"][role],
        service=ROLE_WORKLOAD_SERVICE[role],
        command=command,
        timeout=7200,
    )
    controller_finished = time.monotonic()
    try:
        payload = json.loads(
            result["stdout"],
            object_pairs_hook=strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError(f"{role} combined workload output is invalid") from exc
    expected_fields = {
        "schema_version",
        "status",
        "prefix",
        "user_count",
        "telegram_ratio",
        "target_rps",
        "attempts_per_scenario",
        "write_max_concurrency",
        "read_view_max_concurrency",
        "scenario_count",
        "family_counts",
        "total_business_requests",
        "elapsed_seconds",
        "started_epoch",
        "finished_epoch",
        "aggregate_business_request_rps",
        "min_attempt_start_rps",
        "failed_scenarios",
        "cleanup",
        "reports",
        "production_gate",
        "three_site_writer_fence",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_fields
        or payload.get("schema_version")
        != "bot_webapp_comprehensive_load_matrix_v1"
        or payload.get("status") != "ok"
        or payload.get("prefix") != prefix
        or payload.get("scenario_count") != len(scenario_ids)
        or payload.get("attempts_per_scenario") != attempts_per_scenario
        or payload.get("failed_scenarios") != []
        or payload.get("three_site_writer_fence") is not (role == "webapp_fi")
        or type(payload.get("total_business_requests")) is not int
        or payload["total_business_requests"]
        < len(scenario_ids) * attempts_per_scenario
        or not isinstance(payload.get("cleanup"), dict)
        or payload["cleanup"].get("dry_run") is not False
        or not isinstance(payload.get("reports"), list)
        or [
            str((item.get("scenario") or {}).get("scenario_id"))
            for item in payload["reports"]
        ]
        != list(scenario_ids)
    ):
        raise LiveMatrixError(f"{role} combined workload did not pass exactly")
    return {
        "role": role,
        "controller_started_monotonic": round(controller_started, 6),
        "controller_finished_monotonic": round(controller_finished, 6),
        "result": payload,
    }


def _combined_workload_outcome(
    scenario_id: str,
    observations: dict[str, Any],
) -> dict[str, Any]:
    scenarios = _combined_workload_scenarios(scenario_id)
    workers = observations.get("workers")
    convergence = observations.get("convergence")
    if (
        not isinstance(workers, dict)
        or set(workers) != set(scenarios)
        or not isinstance(convergence, dict)
        or convergence.get("database_business_parity") is not True
        or convergence.get("all_stream_epochs_exactly_applied") is not True
        or convergence.get("unresolved_conflict_count") != 0
    ):
        raise LiveMatrixError("combined workload evidence is incomplete")
    starts = []
    total = 0
    worker_hashes = {}
    for role, expected_scenarios in scenarios.items():
        worker = workers[role]
        payload = worker.get("result") if isinstance(worker, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "ok"
            or payload.get("scenario_count") != len(expected_scenarios)
            or payload.get("failed_scenarios") != []
        ):
            raise LiveMatrixError(f"{role} retained workload result is invalid")
        starts.append(float(worker["controller_started_monotonic"]))
        total += int(payload["total_business_requests"])
        worker_hashes[role] = hash_summary(payload)
    launch_skew = max(starts) - min(starts)
    if launch_skew > 5.0:
        raise LiveMatrixError("Bot/WebApp workload launch skew exceeded five seconds")
    outcome = {
        "two_authority_workers_launched_concurrently": True,
        "controller_launch_skew_seconds": round(launch_skew, 6),
        "business_request_count": total,
        "all_selected_business_scenarios_passed": True,
        "synthetic_business_rows_cleaned": True,
        "three_database_business_parity": True,
        "all_dr_streams_applied_and_acknowledged": True,
        "unresolved_conflict_count": 0,
        "worker_result_sha256": dict(sorted(worker_hashes.items())),
    }
    if scenario_id == "writer_renewal_and_dr_relay_under_load":
        lease = observations.get("writer_lease")
        if (
            not isinstance(lease, dict)
            or set(lease) != {"before", "after", "standby_after"}
        ):
            raise LiveMatrixError("Writer renewal evidence is incomplete")
        before = lease["before"]
        after = lease["after"]
        standby = lease["standby_after"]
        for item in (before, after, standby):
            if not isinstance(item, dict):
                raise LiveMatrixError("Writer renewal state is invalid")
        if (
            before.get("active_site") != "webapp_fi"
            or after.get("active_site") != "webapp_fi"
            or before.get("writer_epoch") != after.get("writer_epoch")
            or before.get("transition_id") != after.get("transition_id")
            or before.get("local_active_with_witness_lease") is not True
            or after.get("local_active_with_witness_lease") is not True
            or int(after.get("lease_refresh_count_for_epoch") or 0)
            <= int(before.get("lease_refresh_count_for_epoch") or 0)
            or str(after.get("witness_lease_issued_at") or "")
            <= str(before.get("witness_lease_issued_at") or "")
            or str(after.get("witness_lease_expires_at") or "")
            <= str(before.get("witness_lease_expires_at") or "")
            or standby.get("active_site") != "webapp_fi"
            or standby.get("writer_epoch") != after.get("writer_epoch")
            or standby.get("control_state") != after.get("control_state")
            or standby.get("local_active_with_witness_lease") is not False
        ):
            raise LiveMatrixError("Writer did not renew safely under DR relay load")
        outcome.update(
            {
                "writer_epoch_stable_during_load": True,
                "writer_transition_stable_during_load": True,
                "witness_lease_renewed_during_load": True,
                "webapp_ir_remained_non_writer": True,
            }
        )
    return outcome


def _writer_lease_observation(
    plan: dict[str, Any],
    role: str,
) -> dict[str, Any]:
    if role not in {"webapp_fi", "webapp_ir"}:
        raise LiveMatrixError("Writer lease observation requires a WebApp role")
    return _site_probe(
        plan,
        role,
        observer=True,
        operation="writer_lease_state",
    )


def _combined_workload_observation(
    args: Any,
    plan: dict[str, Any],
    scenario_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    scenario_map = _combined_workload_scenarios(scenario_id)
    writer_lease_before = (
        _writer_lease_observation(plan, "webapp_fi")
        if scenario_id == "writer_renewal_and_dr_relay_under_load"
        else None
    )
    attempts_per_scenario = (
        600 if scenario_id == "writer_renewal_and_dr_relay_under_load" else 40
    )
    target_rps = (
        10.0 if scenario_id == "writer_renewal_and_dr_relay_under_load" else 150.0
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            role: pool.submit(
                _run_combined_workload_role,
                args,
                plan,
                role=role,
                scenario_ids=scenario_ids,
                attempts_per_scenario=attempts_per_scenario,
                target_rps=target_rps,
            )
            for role, scenario_ids in scenario_map.items()
        }
        workers = {role: futures[role].result() for role in scenario_map}
    convergence, states = _wait_for_business_convergence(plan)
    observations = {
        "workers": workers,
        "convergence": convergence,
        "fresh_convergence_states_sha256": hash_summary(states),
    }
    if writer_lease_before is not None:
        observations["writer_lease"] = {
            "before": writer_lease_before,
            "after": _writer_lease_observation(plan, "webapp_fi"),
            "standby_after": _writer_lease_observation(plan, "webapp_ir"),
        }
    return _combined_workload_outcome(scenario_id, observations), observations


def _combined_workload_verify(
    args: Any,
    plan: dict[str, Any],
    scenario_id: str,
    runner: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    retained = runner.get("doer_observations")
    if not isinstance(retained, dict):
        raise LiveMatrixError("combined workload retained observations are missing")
    expected = _combined_workload_outcome(scenario_id, retained)
    fresh_convergence, fresh_states = _wait_for_business_convergence(
        plan,
        timeout_seconds=180.0,
    )
    independent = {
        "workers": retained["workers"],
        "convergence": fresh_convergence,
        "fresh_convergence_states_sha256": hash_summary(fresh_states),
    }
    if scenario_id == "writer_renewal_and_dr_relay_under_load":
        retained_lease = retained.get("writer_lease")
        if not isinstance(retained_lease, dict):
            raise LiveMatrixError("retained Writer lease evidence is missing")
        independent["writer_lease"] = {
            "before": retained_lease.get("before"),
            "after": _writer_lease_observation(plan, "webapp_fi"),
            "standby_after": _writer_lease_observation(plan, "webapp_ir"),
        }
    observed = _combined_workload_outcome(scenario_id, independent)
    if observed != expected:
        raise LiveMatrixError("combined workload independent oracle differs")
    return observed, independent


def _combined_workload_contract(scenario_id: str) -> dict[str, bool]:
    if scenario_id not in COMBINED_WORKLOAD_LIVE_IDS:
        raise LiveMatrixError("combined workload contract dispatch is incomplete")
    return {
        "bot_fi_foreign_surface_uses_app_database_role": True,
        "webapp_fi_mutations_bound_to_live_writer_and_witness_lease": True,
        "two_authority_workers_launched_concurrently": True,
        "real_router_and_service_business_paths": True,
        "external_provider_boundaries_are_deterministic_staging_noops": True,
        "synthetic_rows_operation_scoped_and_cleaned": True,
        "three_database_convergence_independently_reobserved": True,
        **(
            {
                "sustained_workload_spans_multiple_renew_intervals": True,
                "writer_epoch_and_transition_remain_stable": True,
                "witness_lease_issue_and_expiry_advance": True,
                "webapp_ir_stays_non_writer": True,
            }
            if scenario_id == "writer_renewal_and_dr_relay_under_load"
            else {}
        ),
    }


def _secret_boundary_states(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=len(DATABASE_ROLES)) as pool:
        futures = {
            role: pool.submit(
                _site_probe,
                plan,
                role,
                observer=True,
                operation="secret_boundary_state",
            )
            for role in DATABASE_ROLES
        }
        return {role: futures[role].result() for role in DATABASE_ROLES}


def _secret_boundary_observation(
    args: Any,
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    states = _secret_boundary_states(plan)
    snapshots = collect_all_host_snapshots(plan, args.release_sha)
    fields = {
        "release_compose_sha256",
        "service_count",
        "managed_network_count",
        "secret_values_emitted",
    }
    hashes = set()
    service_counts = set()
    network_counts = set()
    for role, state in states.items():
        if (
            not isinstance(state, dict)
            or set(state) != fields
            or re.fullmatch(
                r"[0-9a-f]{64}",
                str(state.get("release_compose_sha256") or ""),
            )
            is None
            or type(state.get("service_count")) is not int
            or int(state["service_count"]) < 1
            or type(state.get("managed_network_count")) is not int
            or int(state["managed_network_count"]) < 1
            or state.get("secret_values_emitted") is not False
        ):
            raise LiveMatrixError(f"{role} secret boundary observation is invalid")
        hashes.add(str(state["release_compose_sha256"]))
        service_counts.add(int(state["service_count"]))
        network_counts.add(int(state["managed_network_count"]))
    bundles_bound = (
        set(snapshots) == {"bot_fi", "webapp_fi", "webapp_ir", "witness"}
        and all(
            snapshot.get("release_sha") == args.release_sha
            and snapshot.get("clean") is True
            and isinstance(snapshot.get("files"), dict)
            and len(snapshot["files"]) == 2
            for snapshot in snapshots.values()
        )
    )
    if (
        len(hashes) != 1
        or len(service_counts) != 1
        or len(network_counts) != 1
        or not bundles_bound
    ):
        raise LiveMatrixError("cross-service secret boundary evidence differs")
    outcome = {
        "release_compose_boundary_verified_on_three_database_sites": True,
        "release_compose_sha256": next(iter(hashes)),
        "service_count": next(iter(service_counts)),
        "managed_network_count": next(iter(network_counts)),
        "four_live_role_bundles_release_bound": True,
        "secret_values_emitted": False,
    }
    return outcome, {
        "site_boundary_observations": states,
        "four_live_host_snapshots": snapshots,
    }


def _nonnegative_count(value: Any, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise LiveMatrixError(f"{label} is not a non-negative integer")
    return value


def _count_map(value: Any, *, label: str) -> dict[str, int]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str)
        or not key
        or type(count) is not int
        or count < 0
        for key, count in value.items()
    ):
        raise LiveMatrixError(f"{label} status counts are invalid")
    return {str(key): int(count) for key, count in sorted(value.items())}


def _convergence_outcome(
    states: dict[str, dict[str, Any]],
    *,
    scenario_id: str,
) -> dict[str, Any]:
    if set(states) != set(DATABASE_ROLES):
        raise LiveMatrixError("convergence observation does not cover all databases")
    required = {
        "database_business_sha256",
        "database_table_set_sha256",
        "database_table_count",
        "database_row_count",
        "source_streams",
        "destination_streams",
        "unresolved_conflict_count",
        "blob_set_sha256",
        "blob_count",
        "blob_manifest_count",
        "blob_readback_count",
        "event_delivery_status_counts",
        "effect_status_counts",
        "telegram_job_status_counts",
        "writer_state_sha256",
        "writer_state",
        "runtime_producer_epoch",
    }
    sha_re = re.compile(r"[0-9a-f]{64}\Z")
    source_streams: dict[tuple[str, int, str], tuple[int, str]] = {}
    destination_streams: dict[tuple[str, int, str], tuple[int, int, str, str]] = {}
    database_hashes: dict[str, str] = {}
    table_set_hashes: dict[str, str] = {}
    database_rows: dict[str, int] = {}
    database_tables: dict[str, int] = {}
    blob_hashes: dict[str, str] = {}
    blob_counts: dict[str, int] = {}
    conflict_count = 0
    non_acknowledged_deliveries = 0
    nonterminal_effects = 0
    successful_effects = 0
    unresolved_queue_jobs = 0
    successful_queue_jobs = 0
    writer_states: dict[str, list[dict[str, Any]]] = {}
    for role, state in states.items():
        if not isinstance(state, dict) or set(state) != required:
            raise LiveMatrixError(f"{role} convergence state shape is invalid")
        for field in (
            "database_business_sha256",
            "database_table_set_sha256",
            "blob_set_sha256",
            "writer_state_sha256",
        ):
            if sha_re.fullmatch(str(state.get(field) or "")) is None:
                raise LiveMatrixError(f"{role} convergence hash is invalid")
        database_hashes[role] = str(state["database_business_sha256"])
        table_set_hashes[role] = str(state["database_table_set_sha256"])
        database_rows[role] = _nonnegative_count(
            state["database_row_count"],
            label=f"{role} database row count",
        )
        database_tables[role] = _nonnegative_count(
            state["database_table_count"],
            label=f"{role} database table count",
        )
        blob_hashes[role] = str(state["blob_set_sha256"])
        blob_counts[role] = _nonnegative_count(
            state["blob_count"],
            label=f"{role} blob count",
        )
        manifest_count = _nonnegative_count(
            state["blob_manifest_count"],
            label=f"{role} blob manifest count",
        )
        readback_count = _nonnegative_count(
            state["blob_readback_count"],
            label=f"{role} blob readback count",
        )
        if manifest_count != readback_count or readback_count != blob_counts[role]:
            raise LiveMatrixError(f"{role} blob manifest/read-back counts differ")
        conflict_count += _nonnegative_count(
            state["unresolved_conflict_count"],
            label=f"{role} conflict count",
        )
        delivery_counts = _count_map(
            state["event_delivery_status_counts"],
            label=f"{role} event delivery",
        )
        non_acknowledged_deliveries += sum(
            count for status, count in delivery_counts.items()
            if status != "acknowledged"
        )
        effect_counts = _count_map(
            state["effect_status_counts"],
            label=f"{role} effect",
        )
        successful_effects += effect_counts.get("succeeded", 0)
        nonterminal_effects += sum(
            count for status, count in effect_counts.items()
            if status not in {"succeeded", "cancelled_stale_epoch"}
        )
        queue_counts = _count_map(
            state["telegram_job_status_counts"],
            label=f"{role} Telegram queue",
        )
        successful_queue_jobs += (
            queue_counts.get("sent", 0) + queue_counts.get("sent_noop", 0)
        )
        unresolved_queue_jobs += sum(
            count for status, count in queue_counts.items()
            if status
            not in {
                "sent",
                "sent_noop",
                "superseded",
                "expired_interaction",
                "permanent_undeliverable",
                "terminal_failed",
            }
        )
        writer_state = state["writer_state"]
        if not isinstance(writer_state, list):
            raise LiveMatrixError(f"{role} writer state is invalid")
        writer_states[role] = writer_state
        _nonnegative_count(
            state["runtime_producer_epoch"],
            label=f"{role} producer epoch",
        )
        raw_sources = state["source_streams"]
        raw_destinations = state["destination_streams"]
        if not isinstance(raw_sources, list) or not isinstance(raw_destinations, list):
            raise LiveMatrixError(f"{role} stream observation is invalid")
        for row in raw_sources:
            fields = {
                "origin_site",
                "producer_epoch",
                "destination_site",
                "source_sequence",
                "source_transaction_hash",
            }
            if not isinstance(row, dict) or set(row) != fields:
                raise LiveMatrixError(f"{role} source stream shape is invalid")
            epoch = _nonnegative_count(
                row["producer_epoch"],
                label=f"{role} source epoch",
            )
            sequence = _nonnegative_count(
                row["source_sequence"],
                label=f"{role} source sequence",
            )
            digest = str(row["source_transaction_hash"])
            key = (str(row["origin_site"]), epoch, str(row["destination_site"]))
            if (
                epoch < 1
                or key[0] != role
                or key[2] not in set(DATABASE_ROLES) - {role}
                or key in source_streams
                or sha_re.fullmatch(digest) is None
                or (sequence == 0) != (digest == "0" * 64)
            ):
                raise LiveMatrixError(f"{role} source stream identity is invalid")
            source_streams[key] = (sequence, digest)
        for row in raw_destinations:
            fields = {
                "origin_site",
                "producer_epoch",
                "destination_site",
                "received_sequence",
                "applied_sequence",
                "received_transaction_hash",
                "applied_transaction_hash",
            }
            if not isinstance(row, dict) or set(row) != fields:
                raise LiveMatrixError(f"{role} destination stream shape is invalid")
            epoch = _nonnegative_count(
                row["producer_epoch"],
                label=f"{role} destination epoch",
            )
            received = _nonnegative_count(
                row["received_sequence"],
                label=f"{role} received sequence",
            )
            applied = _nonnegative_count(
                row["applied_sequence"],
                label=f"{role} applied sequence",
            )
            received_hash = str(row["received_transaction_hash"])
            applied_hash = str(row["applied_transaction_hash"])
            key = (str(row["origin_site"]), epoch, str(row["destination_site"]))
            if (
                epoch < 1
                or key[0] not in set(DATABASE_ROLES) - {role}
                or key[2] != role
                or key in destination_streams
                or applied > received
                or sha_re.fullmatch(received_hash) is None
                or sha_re.fullmatch(applied_hash) is None
                or (received == 0) != (received_hash == "0" * 64)
                or (applied == 0) != (applied_hash == "0" * 64)
            ):
                raise LiveMatrixError(f"{role} destination stream identity is invalid")
            destination_streams[key] = (
                received,
                applied,
                received_hash,
                applied_hash,
            )
    streams_converged = bool(source_streams) and set(source_streams) == set(
        destination_streams
    )
    if streams_converged:
        for key, (source_sequence, source_hash) in source_streams.items():
            received, applied, received_hash, applied_hash = destination_streams[key]
            if (
                len({source_sequence, received, applied}) != 1
                or len({source_hash, received_hash, applied_hash}) != 1
            ):
                streams_converged = False
                break
    database_parity = (
        len(set(database_hashes.values())) == 1
        and len(set(table_set_hashes.values())) == 1
        and len(set(database_rows.values())) == 1
        and len(set(database_tables.values())) == 1
        and next(iter(database_tables.values()), 0) > 0
    )
    blob_parity = (
        blob_counts["bot_fi"] == 0
        and blob_counts["webapp_fi"] > 0
        and blob_counts["webapp_fi"] == blob_counts["webapp_ir"]
        and blob_hashes["webapp_fi"] == blob_hashes["webapp_ir"]
    )
    webapp_writer_equal = (
        writer_states["webapp_fi"] == writer_states["webapp_ir"]
        and len(writer_states["webapp_fi"]) == 1
    )
    gates_passed = (
        streams_converged
        and conflict_count == 0
        and non_acknowledged_deliveries == 0
        and nonterminal_effects == 0
        and successful_effects > 0
    )
    queue_reconciled = unresolved_queue_jobs == 0 and successful_queue_jobs > 0
    exact_pass = {
        "applied_checkpoint_conflict_effect_gates": gates_passed,
        "database_and_blob_final_parity": (
            gates_passed and database_parity and blob_parity
        ),
        "queue_jobs_effects_conflicts_reconciled": (
            gates_passed and queue_reconciled
        ),
    }.get(scenario_id)
    if exact_pass is not True:
        raise LiveMatrixError(f"{scenario_id} exact live convergence gate did not pass")
    return {
        "scenario_gate_passed": True,
        "all_stream_epochs_exactly_applied": streams_converged,
        "stream_count": len(source_streams),
        "unresolved_conflict_count": conflict_count,
        "non_acknowledged_delivery_count": non_acknowledged_deliveries,
        "nonterminal_effect_count": nonterminal_effects,
        "successful_effect_count": successful_effects,
        "database_business_parity": database_parity,
        "database_business_sha256": next(iter(database_hashes.values())),
        "database_table_set_sha256": next(iter(table_set_hashes.values())),
        "database_table_count": next(iter(database_tables.values())),
        "database_row_count": next(iter(database_rows.values())),
        "webapp_blob_parity": blob_parity,
        "webapp_blob_set_sha256": blob_hashes["webapp_fi"],
        "webapp_blob_count": blob_counts["webapp_fi"],
        "queue_reconciled": queue_reconciled,
        "successful_queue_job_count": successful_queue_jobs,
        "webapp_writer_state_equal": webapp_writer_equal,
    }


def _convergence_contract(scenario_id: str) -> dict[str, bool]:
    common = {
        "repeatable_read_readonly_snapshot_per_database": True,
        "all_stream_epochs_tail_hash_and_sequence_equal": True,
        "unresolved_conflicts_zero": True,
        "unacknowledged_event_deliveries_zero": True,
        "nonterminal_effects_zero": True,
    }
    if scenario_id == "database_and_blob_final_parity":
        return {
            **common,
            "three_site_deep_business_fingerprint_equal": True,
            "webapp_blob_manifest_set_exact": True,
            "every_webapp_blob_locally_rehashed": True,
            "bot_fi_has_no_webapp_blob_replica": True,
        }
    if scenario_id == "queue_jobs_effects_conflicts_reconciled":
        return {
            **common,
            "telegram_jobs_have_no_unresolved_state": True,
            "successful_queue_delivery_nonvacuous": True,
        }
    return common


def _final_writer_route_outcome(
    *,
    convergence: dict[str, Any],
    writer_fi: dict[str, Any],
    writer_ir: dict[str, Any],
    host_snapshots: dict[str, Any],
) -> dict[str, Any]:
    """Prove the normal post-cycle single-writer route from fresh state only."""

    if set(host_snapshots) != set(ROLE_NAMES):
        raise LiveMatrixError("final writer route lacks all host snapshots")
    required_writer = {
        "active_site", "writer_epoch", "control_state", "transition_id",
        "witness_lease_id_sha256", "witness_lease_issued_at",
        "witness_lease_expires_at", "witness_proof_hash",
        "lease_refresh_count_for_epoch", "database_now",
        "local_active_with_witness_lease", "local_active_reasons",
    }
    if set(writer_fi) != required_writer or set(writer_ir) != required_writer:
        raise LiveMatrixError("final writer lease evidence shape is invalid")
    epoch = writer_fi.get("writer_epoch")
    transition = writer_fi.get("transition_id")
    lease_fields = (
        "active_site", "writer_epoch", "control_state", "transition_id",
        "witness_lease_id_sha256", "witness_lease_issued_at",
        "witness_lease_expires_at", "witness_proof_hash",
    )
    host_fault_free = all(
        snapshot.get("managed_fault_container_count") == 0
        and snapshot.get("managed_fault_network_count") == 0
        for snapshot in host_snapshots.values()
    )
    if (
        convergence.get("scenario_gate_passed") is not True
        or convergence.get("all_stream_epochs_exactly_applied") is not True
        or convergence.get("webapp_writer_state_equal") is not True
        or writer_fi.get("active_site") != "webapp_fi"
        or writer_fi.get("control_state") != "active"
        or type(epoch) is not int
        or epoch < 1
        or not isinstance(transition, str)
        or not transition
        or writer_fi.get("local_active_with_witness_lease") is not True
        or writer_ir.get("local_active_with_witness_lease") is not False
        or writer_ir.get("local_active_reasons") != ["writer_active_site_mismatch"]
        or any(writer_fi.get(name) != writer_ir.get(name) for name in lease_fields)
        or type(writer_fi.get("lease_refresh_count_for_epoch")) is not int
        or writer_fi["lease_refresh_count_for_epoch"] < 1
        or not host_fault_free
    ):
        raise LiveMatrixError("final normal writer/standby route is not safe")
    return {
        "normal_writer_site": "webapp_fi",
        "standby_site": "webapp_ir",
        "writer_epoch": epoch,
        "transition_id": transition,
        "witness_lease_sha256": writer_fi["witness_lease_id_sha256"],
        "writer_route_is_active_and_fenced": True,
        "webapp_ir_is_verified_standby": True,
        "writer_state_replicated_exactly": True,
        "all_stream_epochs_exactly_applied": True,
        "managed_fault_residue_zero": True,
    }


def _final_writer_route_observation(
    args: Any,
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    convergence, states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    writer_fi = _writer_lease_observation(plan, "webapp_fi")
    writer_ir = _writer_lease_observation(plan, "webapp_ir")
    snapshots = collect_all_host_snapshots(plan, args.release_sha)
    outcome = _final_writer_route_outcome(
        convergence=convergence,
        writer_fi=writer_fi,
        writer_ir=writer_ir,
        host_snapshots=snapshots,
    )
    return outcome, {
        "convergence": convergence,
        "convergence_states_sha256": hash_summary(states),
        "writer_fi": writer_fi,
        "writer_ir": writer_ir,
        "host_snapshots": snapshots,
    }


def _validate_origin_local_probe(
    value: Any,
    *,
    role_name: str,
    release_sha: str,
) -> dict[str, Any]:
    fields = {
        "schema",
        "status",
        "site",
        "release_sha",
        "origin_tls_status",
        "origin_cache_control",
        "application_ready",
        "application_physical_site",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != ORIGIN_LOCAL_PROBE_SCHEMA
        or value.get("status") != "passed"
        or value.get("site") != role_name
        or value.get("release_sha") != release_sha
        or value.get("origin_tls_status") != 204
        or "no-store" not in str(value.get("origin_cache_control") or "").lower()
        or value.get("application_ready") is not True
        or value.get("application_physical_site") != role_name
    ):
        raise LiveMatrixError(f"{role_name} local origin probe is invalid")
    return value


def _origin_local_probe(plan: dict[str, Any], role_name: str) -> dict[str, Any]:
    if role_name not in {"webapp_fi", "webapp_ir"}:
        raise LiveMatrixError("local ingress probe target is invalid")
    role = plan["_roles"][role_name]
    if role.get("transport") == "object-storage-agent":
        response = run_role_agent_operation(
            role_name,
            role,
            operation="origin_local_probe",
            context={},
            attempt=1,
            timeout=180,
        )
        site_result = response.get("result")
        payload = site_result.get("result") if isinstance(site_result, dict) else None
        origin = payload.get("origin") if isinstance(payload, dict) else None
    else:
        port = "8212" if role_name == "webapp_fi" else "8213"
        result = run_role_command(
            role_name,
            role,
            [
                "/usr/bin/python3",
                "-I",
                "-B",
                f"{str(role['repo_root']).rstrip('/')}/scripts/full_matrix_live/origin_probe.py",
                "--site",
                role_name,
                "--release-sha",
                str(plan["release_sha"]),
                "--port",
                port,
            ],
            timeout=180,
        )
        try:
            origin = json.loads(result["stdout"], object_pairs_hook=strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveMatrixError(f"{role_name} local origin probe output is invalid") from exc
    return _validate_origin_local_probe(
        origin,
        role_name=role_name,
        release_sha=str(plan["release_sha"]),
    )


def _public_ingress_probe(
    plan: dict[str, Any],
    *,
    expected_active_origin: str | None = None,
) -> dict[str, Any]:
    from scripts.full_matrix_live.public_ingress_probe import (
        PublicIngressProbeError,
        probe,
    )

    ingress = plan["_ingress"]
    expected = (
        str(ingress["expected_active_origin"])
        if expected_active_origin is None
        else expected_active_origin
    )
    if expected not in {"webapp_fi", "webapp_ir"}:
        raise LiveMatrixError("public ingress expected Writer is invalid")
    try:
        value = probe(
            release_sha=str(plan["release_sha"]),
            expected_active_origin=expected,
            client_auth_file=Path(str(ingress["client_auth_file"])),
            client_auth_sha256=str(ingress["client_auth_sha256"]),
        )
    except PublicIngressProbeError as exc:
        raise LiveMatrixError("public Full Matrix ingress probe did not pass") from exc
    fields = {
        "schema",
        "status",
        "public_host",
        "expected_active_origin",
        "release_sha",
        "http_status",
        "origin_ready",
        "writer_epoch",
        "response_sha256",
        "repeated_health_status",
        "dynamic_config_status",
        "dynamic_cache_no_store",
        "health_cache_not_stale",
        "canonical_frontend_url",
        "canonical_cors_origin",
        "basic_auth_enforced",
        "dev_login_denied",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != PUBLIC_INGRESS_PROBE_SCHEMA
        or value.get("status") != "passed"
        or value.get("public_host") != "app.gold-trading.ir"
        or value.get("expected_active_origin") != expected
        or value.get("release_sha") != plan["release_sha"]
        or value.get("http_status") != 200
        or value.get("origin_ready") is not True
        or type(value.get("writer_epoch")) is not int
        or value["writer_epoch"] < 1
        or value.get("repeated_health_status") != 200
        or value.get("dynamic_config_status") != 200
        or value.get("dynamic_cache_no_store") is not True
        or value.get("health_cache_not_stale") is not True
        or value.get("canonical_frontend_url") is not True
        or value.get("canonical_cors_origin") is not True
        or value.get("basic_auth_enforced") is not True
        or value.get("dev_login_denied") is not True
        or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("response_sha256") or ""))
    ):
        raise LiveMatrixError("public Full Matrix ingress evidence is invalid")
    return value


def _writer_lifecycle_path(plan: dict[str, Any]) -> Path:
    root = Path(plan["_state_root"])
    if not root.is_absolute() or root.is_symlink():
        raise LiveMatrixError("Writer lifecycle state root is unsafe")
    return root / "writer-lifecycle.json"


def _writer_lifecycle_state(plan: dict[str, Any]) -> dict[str, Any]:
    """Read the one campaign-scoped IR-active checkpoint without guessing it."""

    path = _writer_lifecycle_path(plan)
    try:
        raw = safe_read(
            path,
            label="Full Matrix Writer lifecycle checkpoint",
            owner_only=True,
            max_size=64 * 1024,
        )
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    except Exception as exc:
        raise LiveMatrixError("Writer lifecycle checkpoint is invalid") from exc
    required = {
        "schema",
        "campaign_id",
        "release_sha",
        "iteration",
        "phase",
        "promotion_operation_id",
        "promotion_plan_hash",
        "writer_epoch_before",
        "writer_epoch_after",
        "connectivity_mode",
        "connectivity_consecutive_rounds",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema") != WRITER_LIFECYCLE_SCHEMA
        or value.get("campaign_id") != plan["campaign_id"]
        or value.get("release_sha") != plan["release_sha"]
        or value.get("iteration") not in {1, 2}
        or value.get("phase") != "ir_active"
        or re.fullmatch(
            r"[0-9a-f-]{36}", str(value.get("promotion_operation_id") or "")
        ) is None
        or re.fullmatch(
            r"[0-9a-f]{64}", str(value.get("promotion_plan_hash") or "")
        ) is None
        or type(value.get("writer_epoch_before")) is not int
        or type(value.get("writer_epoch_after")) is not int
        or int(value["writer_epoch_before"]) < 1
        or int(value["writer_epoch_after"])
        != int(value["writer_epoch_before"]) + 1
        or value.get("connectivity_mode") != "isolated"
        or type(value.get("connectivity_consecutive_rounds")) is not int
        or int(value["connectivity_consecutive_rounds"]) < 3
    ):
        raise LiveMatrixError("Writer lifecycle checkpoint differs from the campaign")
    return value


def _write_writer_lifecycle(plan: dict[str, Any], state: dict[str, Any]) -> None:
    path = _writer_lifecycle_path(plan)
    if path.exists() or path.is_symlink():
        raise LiveMatrixError("Writer lifecycle checkpoint already exists")
    raw = (
        json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    write_secure_atomic_bytes(
        path,
        raw,
        label="Full Matrix Writer lifecycle checkpoint",
        mode=0o600,
        max_size=64 * 1024,
    )


def _remove_writer_lifecycle(plan: dict[str, Any]) -> None:
    path = _writer_lifecycle_path(plan)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LiveMatrixError("Writer lifecycle checkpoint disappeared before cleanup") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise LiveMatrixError("Writer lifecycle checkpoint is unsafe to remove")
    path.unlink()


def _writer_pair_observation(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            role: pool.submit(_writer_lease_observation, plan, role)
            for role in ("webapp_fi", "webapp_ir")
        }
        values = {role: futures[role].result() for role in futures}
    fi, ir = values["webapp_fi"], values["webapp_ir"]
    if (
        fi.get("active_site") != ir.get("active_site")
        or type(fi.get("writer_epoch")) is not int
        or fi.get("writer_epoch") != ir.get("writer_epoch")
        or fi.get("control_state") != "active"
        or ir.get("control_state") != "active"
    ):
        raise LiveMatrixError("WebApp Writer observations are inconsistent")
    return values


def _assert_writer_pair(
    observations: dict[str, dict[str, Any]],
    *,
    active_site: str,
    writer_epoch: int,
) -> None:
    if active_site not in {"webapp_fi", "webapp_ir"} or writer_epoch < 1:
        raise LiveMatrixError("Writer lifecycle expectation is invalid")
    standby = "webapp_ir" if active_site == "webapp_fi" else "webapp_fi"
    active = observations.get(active_site)
    inactive = observations.get(standby)
    if (
        not isinstance(active, dict)
        or not isinstance(inactive, dict)
        or active.get("active_site") != active_site
        or inactive.get("active_site") != active_site
        or active.get("writer_epoch") != writer_epoch
        or inactive.get("writer_epoch") != writer_epoch
        or active.get("local_active_with_witness_lease") is not True
        or inactive.get("local_active_with_witness_lease") is not False
    ):
        raise LiveMatrixError("Writer lifecycle state is not Witness-safe")


def _promotion_outcome() -> dict[str, bool]:
    return {
        "schedule_bound_ir_promotion_completed": True,
        "isolated_connectivity_threshold_was_attested": True,
        "webapp_ir_is_the_only_witness_leased_writer": True,
        "webapp_fi_is_nonwriting_standby": True,
        "public_ingress_is_routed_to_webapp_ir": True,
    }


def _failback_outcome() -> dict[str, bool]:
    return {
        "schedule_bound_fi_failback_completed": True,
        "webapp_fi_is_the_only_witness_leased_writer": True,
        "webapp_ir_is_nonwriting_standby": True,
        "public_ingress_is_routed_to_webapp_fi": True,
        "writer_lifecycle_checkpoint_removed": True,
    }


def _promote_ir_lifecycle(args: Any, plan: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    """Perform the catalog's one real FI→IR lifecycle transition.

    This is intentionally tied to the exact ``iran_international`` schedule
    entry.  It never accepts a caller-selected site, action, epoch, provider
    route, or transport.  The retained checkpoint lets later IR-active
    scenarios prove their precondition without replaying a promotion.
    """

    path = _writer_lifecycle_path(plan)
    if path.exists() or path.is_symlink():
        state = _writer_lifecycle_state(plan)
        if state["iteration"] != args.iteration:
            raise LiveMatrixError("Writer lifecycle checkpoint belongs to another iteration")
        pair = _writer_pair_observation(plan)
        _assert_writer_pair(
            pair,
            active_site="webapp_ir",
            writer_epoch=int(state["writer_epoch_after"]),
        )
        public = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
        return _promotion_outcome(), {
            "promotion": {
                "operation_id": state["promotion_operation_id"],
                "plan_hash": state["promotion_plan_hash"],
                "writer_epoch_before": state["writer_epoch_before"],
                "writer_epoch_after": state["writer_epoch_after"],
                "connectivity_mode": state["connectivity_mode"],
                "connectivity_consecutive_rounds": state[
                    "connectivity_consecutive_rounds"
                ],
            },
            "writer_pair": pair,
            "public_ingress": public,
            "resumed_ir_active_checkpoint": True,
        }
    before = _writer_pair_observation(plan)
    before_epoch = int(before["webapp_fi"]["writer_epoch"])
    _assert_writer_pair(before, active_site="webapp_fi", writer_epoch=before_epoch)
    promotion = execute_transition(
        plan,
        scenario_id="iran_international_cutoff_promotes_ir",
        iteration=args.iteration,
        action="promote_ir",
    )
    if (
        promotion.get("status") != "completed"
        or promotion.get("source_site") != "webapp_fi"
        or promotion.get("target_site") != "webapp_ir"
        or promotion.get("writer_epoch_before") != before_epoch
        or promotion.get("writer_epoch_after") != before_epoch + 1
        or promotion.get("connectivity_mode") != "isolated"
        or int(promotion.get("connectivity_consecutive_rounds") or 0) < 3
    ):
        raise LiveMatrixError("scheduled Iran-isolation promotion did not complete safely")
    after = _writer_pair_observation(plan)
    _assert_writer_pair(after, active_site="webapp_ir", writer_epoch=before_epoch + 1)
    public = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    _write_writer_lifecycle(
        plan,
        {
            "schema": WRITER_LIFECYCLE_SCHEMA,
            "campaign_id": plan["campaign_id"],
            "release_sha": plan["release_sha"],
            "iteration": args.iteration,
            "phase": "ir_active",
            "promotion_operation_id": promotion["operation_id"],
            "promotion_plan_hash": promotion["plan_hash"],
            "writer_epoch_before": before_epoch,
            "writer_epoch_after": before_epoch + 1,
            "connectivity_mode": promotion["connectivity_mode"],
            "connectivity_consecutive_rounds": promotion[
                "connectivity_consecutive_rounds"
            ],
        },
    )
    return _promotion_outcome(), {
        "promotion": {
            key: promotion[key]
            for key in (
                "operation_id",
                "plan_hash",
                "writer_epoch_before",
                "writer_epoch_after",
                "connectivity_mode",
                "connectivity_consecutive_rounds",
            )
        },
        "writer_before": before,
        "writer_after": after,
        "public_ingress": public,
        "writer_lifecycle_checkpoint_retained": True,
    }


def _failback_fi_lifecycle(args: Any, plan: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    """Return the catalog from IR-active recovery to the normal FI Writer."""

    state = _writer_lifecycle_state(plan)
    if state["iteration"] != args.iteration:
        raise LiveMatrixError("Writer lifecycle checkpoint belongs to another iteration")
    before = _writer_pair_observation(plan)
    before_epoch = int(state["writer_epoch_after"])
    _assert_writer_pair(before, active_site="webapp_ir", writer_epoch=before_epoch)
    failback = execute_transition(
        plan,
        scenario_id="fi_epoch_reacquire_and_route_switch",
        iteration=args.iteration,
        action="failback_fi",
    )
    if (
        failback.get("status") != "completed"
        or failback.get("source_site") != "webapp_ir"
        or failback.get("target_site") != "webapp_fi"
        or failback.get("writer_epoch_before") != before_epoch
        or failback.get("writer_epoch_after") != before_epoch + 1
    ):
        raise LiveMatrixError("scheduled FI failback did not complete safely")
    after = _writer_pair_observation(plan)
    _assert_writer_pair(after, active_site="webapp_fi", writer_epoch=before_epoch + 1)
    public = _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    _remove_writer_lifecycle(plan)
    return _failback_outcome(), {
        "promotion": {
            "operation_id": state["promotion_operation_id"],
            "plan_hash": state["promotion_plan_hash"],
            "writer_epoch_before": state["writer_epoch_before"],
            "writer_epoch_after": state["writer_epoch_after"],
        },
        "failback": {
            key: failback[key]
            for key in (
                "operation_id",
                "plan_hash",
                "writer_epoch_before",
                "writer_epoch_after",
                "connectivity_mode",
                "connectivity_consecutive_rounds",
            )
        },
        "writer_before": before,
        "writer_after": after,
        "public_ingress": public,
        "writer_lifecycle_checkpoint_removed": True,
    }


def _verify_promoted_ir_lifecycle(
    args: Any,
    plan: dict[str, Any],
    runner: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    retained = runner.get("doer_observations")
    if not isinstance(retained, dict) or not isinstance(retained.get("promotion"), dict):
        raise LiveMatrixError("retained IR promotion evidence is incomplete")
    state = _writer_lifecycle_state(plan)
    if state["iteration"] != args.iteration:
        raise LiveMatrixError("Writer lifecycle checkpoint belongs to another iteration")
    promotion = retained["promotion"]
    if (
        promotion.get("operation_id") != state["promotion_operation_id"]
        or promotion.get("plan_hash") != state["promotion_plan_hash"]
        or promotion.get("writer_epoch_after") != state["writer_epoch_after"]
        or promotion.get("connectivity_mode") != "isolated"
        or int(promotion.get("connectivity_consecutive_rounds") or 0) < 3
    ):
        raise LiveMatrixError("retained IR promotion differs from lifecycle checkpoint")
    pair = _writer_pair_observation(plan)
    _assert_writer_pair(pair, active_site="webapp_ir", writer_epoch=int(state["writer_epoch_after"]))
    public = _public_ingress_probe(plan, expected_active_origin="webapp_ir")
    return _promotion_outcome(), {
        "writer_pair": pair,
        "public_ingress": public,
        "promotion_checkpoint_sha256": hash_summary(state),
    }


def _verify_failed_back_to_fi_lifecycle(
    args: Any,
    plan: dict[str, Any],
    runner: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    retained = runner.get("doer_observations")
    if not isinstance(retained, dict) or not isinstance(retained.get("failback"), dict):
        raise LiveMatrixError("retained FI failback evidence is incomplete")
    if _writer_lifecycle_path(plan).exists():
        raise LiveMatrixError("FI failback retained an IR-active lifecycle checkpoint")
    failback = retained["failback"]
    epoch = failback.get("writer_epoch_after")
    if type(epoch) is not int or epoch < 2:
        raise LiveMatrixError("retained FI failback epoch is invalid")
    pair = _writer_pair_observation(plan)
    _assert_writer_pair(pair, active_site="webapp_fi", writer_epoch=epoch)
    public = _public_ingress_probe(plan, expected_active_origin="webapp_fi")
    return _failback_outcome(), {
        "writer_pair": pair,
        "public_ingress": public,
        "retained_failback_operation_id": failback.get("operation_id"),
        "retained_failback_plan_hash": failback.get("plan_hash"),
    }


def _ingress_route_observation(
    plan: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            role: pool.submit(_origin_local_probe, plan, role)
            for role in ("webapp_fi", "webapp_ir")
        }
        origins = {role: futures[role].result() for role in futures}
    public = _public_ingress_probe(plan)
    outcome = {
        "both_origins_local_tls_health_passed": all(
            item["origin_tls_status"] == 204 for item in origins.values()
        ),
        "both_origins_same_release_and_local_data_plane_ready": all(
            item["release_sha"] == plan["release_sha"]
            and item["application_ready"] is True
            and item["application_physical_site"] == role
            for role, item in origins.items()
        ),
        "public_cdn_ingress_reaches_normal_fi_writer": (
            public["origin_ready"] is True
            and public["expected_active_origin"] == "webapp_fi"
            and public["release_sha"] == plan["release_sha"]
        ),
    }
    if any(value is not True for value in outcome.values()):
        raise LiveMatrixError("Full Matrix ingress route did not satisfy its contract")
    return outcome, {"local_origins": origins, "public_ingress": public}


def _ingress_route_contract() -> dict[str, bool]:
    return {
        "both_origins_tested_locally_with_public_tls_sni": True,
        "both_origins_release_and_application_readiness_attested": True,
        "public_basic_auth_ingress_attests_fi_writer_and_global_convergence": True,
        "no_finland_to_iran_payload_transport_used": True,
    }


def _retained_artifact_chain(args: Any) -> list[dict[str, Any]]:
    root = Path(args.artifact_root)
    if not root.is_absolute() or root.is_symlink():
        raise LiveMatrixError("external anchor artifact root is unsafe")
    metadata = root.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise LiveMatrixError("external anchor artifact root is not owner-only")
    artifacts: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if (
            path.is_symlink()
            or not path.is_file()
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,190}\.json", path.name)
        ):
            raise LiveMatrixError("external anchor artifact directory is contaminated")
        raw = safe_read(
            path,
            label="external anchor retained artifact",
            owner_only=True,
            max_size=32 * 1024 * 1024,
        )
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveMatrixError("external anchor retained artifact is invalid JSON") from exc
        if not isinstance(value, dict):
            raise LiveMatrixError("external anchor retained artifact is not an object")
        artifacts.append(
            {
                "path": path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            }
        )
    if not artifacts:
        raise LiveMatrixError("external anchor has no retained evidence")
    return artifacts


def _artifact_anchor_observation(
    args: Any,
    plan: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    from scripts.full_matrix_live.object_storage_controller import (
        ObjectStorageControllerError,
        store_external_anchor,
    )

    artifacts = _retained_artifact_chain(args)
    controller_config = Path(plan["_bindings"]["object_storage_transport"]["path"])
    try:
        anchor = store_external_anchor(
            controller_config,
            campaign_id=str(plan["campaign_id"]),
            release_sha=str(plan["release_sha"]),
            execution_class=str(plan["execution_class"]),
            operation_id=str(args.operation_id),
            artifacts=artifacts,
        )
    except ObjectStorageControllerError as exc:
        raise LiveMatrixError("external artifact anchor did not pass") from exc
    fields = {
        "status",
        "object_key",
        "object_version_id",
        "anchor_sha256",
        "chain_head",
        "artifact_count",
    }
    if (
        not isinstance(anchor, dict)
        or set(anchor) != fields
        or anchor.get("status") != "anchored"
        or not isinstance(anchor.get("object_key"), str)
        or "/external-anchors/" not in anchor["object_key"]
        or not isinstance(anchor.get("object_version_id"), str)
        or not anchor["object_version_id"]
        or type(anchor.get("artifact_count")) is not int
        or anchor["artifact_count"] != len(artifacts)
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", str(anchor.get(name) or ""))
            for name in ("anchor_sha256", "chain_head")
        )
    ):
        raise LiveMatrixError("external artifact anchor evidence is invalid")
    outcome = artifact_chain_decision(
        ordered_hashes=[item["sha256"] for item in artifacts],
        retained_head=str(anchor["chain_head"]),
        external_anchor=str(anchor["chain_head"]),
    )
    if any(value is not True for value in outcome.values()):
        raise LiveMatrixError("external artifact anchor chain does not verify")
    return outcome, {"artifacts": artifacts, "external_anchor": anchor}


def _artifact_anchor_contract() -> dict[str, bool]:
    return {
        "all_retained_controller_artifacts_hashed_in_name_order": True,
        "chain_head_recomputed_before_anchor": True,
        "private_versioned_object_storage_readback_matches_anchor": True,
        "external_anchor_is_bound_to_campaign_release_and_operation": True,
    }


def _cdn_dynamic_cache_observation(
    plan: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    public = _public_ingress_probe(plan)
    outcome = {
        "two_public_origin_health_reads_are_not_stale": (
            public["repeated_health_status"] == 200
            and public["health_cache_not_stale"] is True
        ),
        "dynamic_api_config_is_not_cacheable": (
            public["dynamic_config_status"] == 200
            and public["dynamic_cache_no_store"] is True
        ),
        "health_read_still_attests_active_fi_writer": (
            public["origin_ready"] is True
            and public["expected_active_origin"] == "webapp_fi"
        ),
    }
    if any(value is not True for value in outcome.values()):
        raise LiveMatrixError("CDN dynamic/stale-health contract did not pass")
    return outcome, {"public_ingress": public}


def _cdn_dynamic_cache_contract() -> dict[str, bool]:
    return {
        "authenticated_dynamic_api_response_has_no_store": True,
        "two_independent_public_health_reads_reject_cached_age": True,
        "public_health_remains_bound_to_normal_active_writer": True,
    }


def _canonical_ingress_observation(
    plan: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    public = _public_ingress_probe(plan)
    outcome = {
        "canonical_staging_frontend_url_returned": public["canonical_frontend_url"] is True,
        "canonical_origin_cors_is_explicit": public["canonical_cors_origin"] is True,
        "unauthenticated_root_is_basic_auth_challenged": public["basic_auth_enforced"] is True,
        "development_login_path_is_denied_at_proxy": public["dev_login_denied"] is True,
    }
    if any(value is not True for value in outcome.values()):
        raise LiveMatrixError("canonical ingress/auth/CORS contract did not pass")
    return outcome, {"public_ingress": public}


def _canonical_ingress_contract() -> dict[str, bool]:
    return {
        "api_config_uses_exact_isolated_public_origin": True,
        "cors_response_reflects_the_exact_canonical_origin": True,
        "root_basic_auth_challenge_observed_without_credentials": True,
        "dev_login_is_not_exposed_by_isolated_origin": True,
    }


def _repeatability_baseline_path(plan: dict[str, Any]) -> Path:
    return plan["_state_root"] / "repeatability-baseline-v1.json"


def _expected_repeatability_scenarios(plan: dict[str, Any]) -> set[str]:
    phases = scenarios_for_execution_class(str(plan["execution_class"]))
    return {
        scenario_id
        for scenario_ids in phases.values()
        for scenario_id in scenario_ids
        if scenario_id != "second_cycle_same_or_stronger_oracles"
    }


def _retained_scenario_assertion_strengths(
    args: Any,
    plan: dict[str, Any],
    *,
    iteration: int,
) -> dict[str, dict[str, str]]:
    root = Path(args.artifact_root)
    expected = _expected_repeatability_scenarios(plan)
    candidates: dict[str, tuple[int, dict[str, str]]] = {}
    for path in root.iterdir():
        if not path.name.endswith("-scenario-evidence.json"):
            continue
        raw = safe_read(
            path,
            label="repeatability retained scenario evidence",
            owner_only=True,
            max_size=32 * 1024 * 1024,
        )
        try:
            evidence = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveMatrixError("repeatability scenario evidence is invalid") from exc
        if (
            isinstance(evidence, dict)
            and evidence.get("schema") == SCENARIO_EVIDENCE_SCHEMA
            and evidence.get("iteration") != iteration
        ):
            continue
        if (
            not isinstance(evidence, dict)
            or evidence.get("schema") != SCENARIO_EVIDENCE_SCHEMA
            or evidence.get("status") != "passed"
            or evidence.get("campaign_id") != plan["campaign_id"]
            or evidence.get("release_sha") != plan["release_sha"]
            or evidence.get("activation_sha") != plan["release_sha"]
            or evidence.get("iteration") != iteration
            or not isinstance(evidence.get("scenario_id"), str)
            or evidence["scenario_id"] not in expected
            or type(evidence.get("attempt")) is not int
            or evidence["attempt"] < 1
            or not isinstance(evidence.get("assertions"), list)
            or not evidence["assertions"]
        ):
            raise LiveMatrixError("repeatability scenario evidence has an invalid identity")
        assertion_contracts: dict[str, str] = {}
        for assertion in evidence["assertions"]:
            if (
                not isinstance(assertion, dict)
                or assertion.get("status") != "passed"
                or not isinstance(assertion.get("name"), str)
                or not assertion["name"]
                or "expected" not in assertion
                or assertion["name"] in assertion_contracts
            ):
                raise LiveMatrixError("repeatability assertion contract is invalid")
            assertion_contracts[assertion["name"]] = hashlib.sha256(
                json_bytes(
                    {"name": assertion["name"], "expected": assertion["expected"]}
                )
            ).hexdigest()
        scenario_id = evidence["scenario_id"]
        current = (evidence["attempt"], assertion_contracts)
        previous = candidates.get(scenario_id)
        if previous is not None and previous[0] == current[0]:
            raise LiveMatrixError("repeatability scenario evidence duplicates an attempt")
        if previous is None or current[0] > previous[0]:
            candidates[scenario_id] = current
    if set(candidates) != expected:
        raise LiveMatrixError("repeatability evidence does not cover every preceding scenario")
    return {scenario_id: candidates[scenario_id][1] for scenario_id in sorted(candidates)}


def _repeatability_baseline(
    plan: dict[str, Any],
    *,
    strengths: dict[str, dict[str, str]],
) -> dict[str, Any]:
    return {
        "schema": "three-site-full-matrix-repeatability-baseline-v1",
        "campaign_id": plan["campaign_id"],
        "release_sha": plan["release_sha"],
        "execution_class": plan["execution_class"],
        "scenario_assertion_contracts": strengths,
    }


def _read_repeatability_baseline(plan: dict[str, Any]) -> dict[str, Any]:
    path = _repeatability_baseline_path(plan)
    raw = safe_read(
        path,
        label="Full Matrix first-cycle repeatability baseline",
        owner_only=True,
        max_size=512 * 1024,
    )
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError("Full Matrix repeatability baseline is invalid JSON") from exc
    fields = {
        "schema",
        "campaign_id",
        "release_sha",
        "execution_class",
        "scenario_assertion_contracts",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or value.get("schema") != "three-site-full-matrix-repeatability-baseline-v1"
        or value.get("campaign_id") != plan["campaign_id"]
        or value.get("release_sha") != plan["release_sha"]
        or value.get("execution_class") != plan["execution_class"]
        or not isinstance(value.get("scenario_assertion_contracts"), dict)
        or set(value["scenario_assertion_contracts"])
        != _expected_repeatability_scenarios(plan)
        or any(
            not isinstance(contracts, dict)
            or not contracts
            or any(
                not isinstance(name, str)
                or not name
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                for name, digest in contracts.items()
            )
            for contracts in value["scenario_assertion_contracts"].values()
        )
    ):
        raise LiveMatrixError("Full Matrix repeatability baseline is invalid")
    return value


def _write_repeatability_baseline(plan: dict[str, Any], value: dict[str, Any]) -> None:
    path = _repeatability_baseline_path(plan)
    raw = json_bytes(value)
    if path.exists():
        existing = safe_read(
            path,
            label="Full Matrix first-cycle repeatability baseline",
            owner_only=True,
            max_size=512 * 1024,
        )
        if existing != raw:
            raise LiveMatrixError("Full Matrix repeatability baseline replay differs")
        return
    write_secure_atomic_bytes(
        path,
        raw,
        label="Full Matrix first-cycle repeatability baseline",
        mode=0o600,
        max_size=512 * 1024,
    )


def _repeatability_observation(
    args: Any,
    plan: dict[str, Any],
    *,
    allow_write: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    iteration = int(args.iteration)
    strengths = _retained_scenario_assertion_strengths(
        args,
        plan,
        iteration=iteration,
    )
    if iteration == 1:
        baseline = _repeatability_baseline(plan, strengths=strengths)
        if allow_write:
            _write_repeatability_baseline(plan, baseline)
        else:
            retained = _read_repeatability_baseline(plan)
            if retained != baseline:
                raise LiveMatrixError("first-cycle repeatability baseline differs")
        return {
            "cycle_state": "first_cycle_baseline_recorded",
            "preceding_scenario_count": len(strengths),
            "same_or_stronger_verified": False,
        }, {"first_cycle_baseline": baseline}
    if iteration != 2:
        raise LiveMatrixError("repeatability scenario iteration is invalid")
    baseline = _read_repeatability_baseline(plan)
    baseline_strengths = baseline["scenario_assertion_contracts"]
    if any(
        not set(baseline_strengths[scenario_id].items()).issubset(
            set(strengths[scenario_id].items())
        )
        for scenario_id in strengths
    ):
        raise LiveMatrixError("second-cycle oracle assertion strength regressed")
    return {
        "cycle_state": "second_cycle_same_or_stronger_verified",
        "preceding_scenario_count": len(strengths),
        "same_or_stronger_verified": True,
    }, {
        "first_cycle_baseline": baseline,
        "second_cycle_assertion_contracts": strengths,
    }


def _repeatability_contract() -> dict[str, bool]:
    return {
        "every_preceding_scenario_has_retained_typed_evidence": True,
        "first_cycle_assertion_strength_baseline_is_owner_only": True,
        "second_cycle_cannot_reduce_any_preceding_oracle_assertion_contract": True,
    }


def _fresh_history_outcome(states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    expected_heads = _release_heads()
    actual_heads = {
        role: value.get("alembic_revisions") for role, value in states.items()
    }
    schema_hashes = {
        role: value.get("schema_sha256") for role, value in states.items()
    }
    schema_counts = {
        role: value.get("schema_column_count") for role, value in states.items()
    }
    passed = (
        set(states) == set(DATABASE_ROLES)
        and all(value == expected_heads for value in actual_heads.values())
        and len(set(schema_hashes.values())) == 1
        and len(set(schema_counts.values())) == 1
        and next(iter(schema_hashes.values()), "") not in {"", "0" * 64}
    )
    return {
        "all_three_database_heads_equal": passed,
        "release_heads": expected_heads,
        "site_heads": actual_heads,
        "schema_sha256_by_site": schema_hashes,
        "schema_column_count_by_site": schema_counts,
    }


def _legacy_clone_observation(
    plan: dict[str, Any],
    *,
    observer: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    states = _migration_states(plan, observer=observer)
    live = _fresh_history_outcome(states)
    migration = plan["_bindings"]["migration_plan"]["payload"]
    global_commit = plan["_bindings"]["global_commit"]["payload"]
    migration_sha256 = plan["_bindings"]["migration_plan"]["sha256"]
    source_backups = migration.get("source_backups")
    target_map = migration.get("target_seed_map")
    rollback = migration.get("rollback_policy")
    committed = global_commit.get("committed_role_states")
    expected_map = {
        ("bot_fi", "bot_fi", "restore"),
        ("webapp_fi", "webapp_fi", "restore"),
        ("webapp_fi", "webapp_ir", "clone"),
        (None, "witness", "empty"),
    }
    source_backup_roles = {
        str(item.get("source_role"))
        for item in source_backups
        if isinstance(item, dict)
    } if isinstance(source_backups, list) else set()
    seed_map = {
        (item.get("source_role"), item.get("target_role"), item.get("mode"))
        for item in target_map
        if isinstance(item, dict)
    } if isinstance(target_map, list) else set()
    all_role_journals_committed = (
        isinstance(committed, dict)
        and set(committed) == {"bot_fi", "webapp_fi", "webapp_ir", "witness"}
        and all(
            isinstance(item, dict)
            and item.get("status") == "committed"
            and item.get("role") == role
            and item.get("release_sha") == plan["release_sha"]
            and item.get("plan_sha256") == migration_sha256
            for role, item in committed.items()
        )
    )
    outcome = {
        "two_legacy_authority_backups_bound": (
            source_backup_roles == {"bot_fi", "webapp_fi"}
            and len(source_backups) == 2
        ),
        "webapp_ir_is_clone_of_webapp_fi": seed_map == expected_map,
        "legacy_sources_retained_for_forward_rollback": (
            isinstance(rollback, dict)
            and rollback.get("legacy_sources_retained") is True
            and rollback.get("routing_held_until_commit") is True
            and rollback.get("alembic_downgrade") is False
        ),
        "four_role_migration_journals_committed": (
            global_commit.get("schema") == "three-site-staging-global-commit-v2"
            and global_commit.get("status") == "passed"
            and global_commit.get("all_roles_committed") is True
            and global_commit.get("release_sha") == plan["release_sha"]
            and global_commit.get("plan_sha256") == migration_sha256
            and all_role_journals_committed
        ),
        "three_live_database_histories_equal": (
            live["all_three_database_heads_equal"] is True
        ),
    }
    if any(value is not True for value in outcome.values()):
        raise LiveMatrixError("legacy staging clone migration evidence is incomplete")
    evidence = {
        "migration_plan_sha256": migration_sha256,
        "global_commit_sha256": plan["_bindings"]["global_commit"]["sha256"],
        "fresh_live_migration_state": live,
    }
    return outcome, evidence


def _privilege_outcome(states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    roles = {
        role: {
            "current_user": value.get("current_user"),
            "only_select_table_grants": value.get("only_select_table_grants"),
            "set_role_denied": value.get("set_role_denied"),
            "create_database_denied": value.get("create_database_denied"),
            "bypass_rls_denied": value.get("bypass_rls_denied"),
        }
        for role, value in states.items()
    }
    passed = set(states) == set(DATABASE_ROLES) and all(
        value == {
            "current_user": f"{role}_observer",
            "only_select_table_grants": True,
            "set_role_denied": True,
            "create_database_denied": True,
            "bypass_rls_denied": True,
        }
        for role, value in roles.items()
    )
    return {
        "least_privilege_roles_attested": passed,
        "observer_roles": roles,
    }


def _four_role_outcome(
    plan: dict[str, Any],
    snapshots: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    inventory_roles = {
        str(item["role"]): item
        for item in plan["_inventory"].get("roles", [])
        if isinstance(item, dict) and "role" in item
    }
    machine_ids = {
        role: value.get("machine_id") for role, value in snapshots.items()
    }
    projects = {
        role: value.get("project") for role, value in snapshots.items()
    }
    host_storage = {
        role: [
            inventory_roles.get(role, {}).get("host_ip"),
            inventory_roles.get(role, {}).get("storage_root"),
        ]
        for role in snapshots
    }
    passed = (
        set(snapshots) == {"bot_fi", "webapp_fi", "webapp_ir", "witness"}
        and set(inventory_roles) == set(snapshots)
        and len(set(machine_ids.values())) == 4
        and len(set(projects.values())) == 4
        and len({tuple(value) for value in host_storage.values()}) == 4
        and all(machine_ids.values())
        and all(projects.values())
    )
    return {
        "four_role_identity_isolated": bool(passed),
        "machine_ids_by_role": machine_ids,
        "compose_projects_by_role": projects,
        "host_storage_identity_by_role": host_storage,
    }


def _snapshot(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "ok",
        "schema_version": 1,
        "mode": "fixture",
        "table_count": 1,
        "max_rows_per_table": len(rows),
        "tables": {
            "offers": build_table_parity_snapshot("offers", rows),
        },
    }


def _migration_fixture_outcome(scenario_id: str) -> dict[str, Any]:
    if scenario_id == "integer_id_collision_fixtures":
        fi = build_sync_metadata(
            "offers",
            41,
            "INSERT",
            {
                "id": 41,
                "offer_public_id": "fm-offer-fi",
                "home_server": "iran",
            },
        )
        ir = build_sync_metadata(
            "offers",
            41,
            "INSERT",
            {
                "id": 41,
                "offer_public_id": "fm-offer-ir",
                "home_server": "iran",
            },
        )
        passed = (
            fi["aggregate_db_id"] == ir["aggregate_db_id"] == 41
            and fi["aggregate_id"] != ir["aggregate_id"]
        )
        return {
            "integer_primary_key_not_global_identity": passed,
            "shared_local_database_id": 41,
            "distinct_aggregate_id_count": len(
                {fi["aggregate_id"], ir["aggregate_id"]}
            ),
        }
    if scenario_id == "natural_identity_cross_site_collision":
        fi = build_sync_metadata(
            "offers",
            41,
            "UPDATE",
            {
                "id": 41,
                "offer_public_id": "fm-shared-natural-id",
                "home_server": "iran",
            },
        )
        ir = build_sync_metadata(
            "offers",
            9001,
            "UPDATE",
            {
                "id": 9001,
                "offer_public_id": "fm-shared-natural-id",
                "home_server": "iran",
            },
        )
        passed = (
            fi["aggregate_db_id"] != ir["aggregate_db_id"]
            and fi["aggregate_id"] == ir["aggregate_id"]
            == "fm-shared-natural-id"
        )
        return {
            "natural_identity_wins_over_local_database_id": passed,
            "distinct_local_database_id_count": len(
                {fi["aggregate_db_id"], ir["aggregate_db_id"]}
            ),
            "aggregate_identity_count": len(
                {fi["aggregate_id"], ir["aggregate_id"]}
            ),
        }
    if scenario_id == "unique_ids_real_business_conflict_quarantined":
        report = compare_parity_snapshots(
            _snapshot(
                [
                    {
                        "id": 41,
                        "offer_public_id": "fm-conflict",
                        "home_server": "iran",
                        "price": "100",
                    }
                ]
            ),
            _snapshot(
                [
                    {
                        "id": 9001,
                        "offer_public_id": "fm-conflict",
                        "home_server": "iran",
                        "price": "101",
                    }
                ]
            ),
        )
        decision = projection_version_decision(
            stored_epoch=7,
            stored_sequence=12,
            stored_origin_site="webapp_fi",
            incoming_epoch=7,
            incoming_sequence=13,
            incoming_origin_site="webapp_ir",
        )
        return {
            "same_natural_identity_business_drift_detected": (
                report["status"] == "business_drift"
                and report["tables"]["offers"]["business_mismatch_count"] == 1
            ),
            "same_epoch_multi_origin_quarantined": decision == "conflict",
            "parity_status": report["status"],
            "projection_decision": decision,
        }
    if scenario_id == "counter_double_increment_fixture":
        base = {
            "_sync_contract": "user_counter_event_v2",
            "_counter_event_kind": "increment",
            "_counter_epoch": 3,
            "_counter_deltas": {"trades_count": 1},
            "_counter_occurred_at": "2026-01-01T00:00:00+00:00",
            "_sync_identity": {
                "current": {"telegram_id": 123456789},
                "previous": {},
            },
        }
        first = build_sync_metadata(
            "users",
            77,
            "UPDATE",
            {**base, "_counter_event_id": "fm-counter-event-one"},
        )
        replay = build_sync_metadata(
            "users",
            9001,
            "UPDATE",
            {**base, "_counter_event_id": "fm-counter-event-one"},
        )
        next_event = build_sync_metadata(
            "users",
            77,
            "UPDATE",
            {**base, "_counter_event_id": "fm-counter-event-two"},
        )
        passed = (
            first["aggregate_id"] == replay["aggregate_id"]
            == "counter-event:fm-counter-event-one"
            and next_event["aggregate_id"] != first["aggregate_id"]
        )
        return {
            "counter_replay_has_one_idempotency_identity": passed,
            "replay_aggregate_identity_count": len(
                {first["aggregate_id"], replay["aggregate_id"]}
            ),
            "independent_event_identity_count": len(
                {first["aggregate_id"], next_event["aggregate_id"]}
            ),
        }
    if scenario_id == "delete_update_resurrection_fixture":
        delete_decision = projection_version_decision(
            stored_epoch=9,
            stored_sequence=20,
            stored_origin_site="webapp_fi",
            incoming_epoch=9,
            incoming_sequence=21,
            incoming_origin_site="webapp_fi",
        )
        delayed_update_decision = projection_version_decision(
            stored_epoch=9,
            stored_sequence=21,
            stored_origin_site="webapp_fi",
            incoming_epoch=9,
            incoming_sequence=20,
            incoming_origin_site="webapp_fi",
        )
        return {
            "newer_delete_projection_applies": delete_decision == "apply",
            "delayed_predelete_update_is_stale": delayed_update_decision == "stale",
            "delete_decision": delete_decision,
            "delayed_update_decision": delayed_update_decision,
        }
    if scenario_id == "backup_counts_pass_semantic_parity_fails":
        local = _snapshot(
            [
                {
                    "id": 41,
                    "offer_public_id": "fm-semantic",
                    "home_server": "iran",
                    "price": "100",
                }
            ]
        )
        peer = _snapshot(
            [
                {
                    "id": 9001,
                    "offer_public_id": "fm-semantic",
                    "home_server": "iran",
                    "price": "101",
                }
            ]
        )
        report = compare_parity_snapshots(local, peer)
        return {
            "row_counts_equal": (
                local["tables"]["offers"]["row_count"]
                == peer["tables"]["offers"]["row_count"]
                == 1
            ),
            "semantic_parity_fails_closed": (
                report["status"] == "business_drift"
                and report["tables"]["offers"]["business_mismatch_count"] == 1
            ),
            "parity_status": report["status"],
        }
    raise LiveMatrixError("migration fixture dispatch is incomplete")


def _migration_fixture_contract(scenario_id: str) -> dict[str, bool]:
    common = {
        "release_projection_policy_exercised": True,
        "release_sync_identity_policy_exercised": True,
        "release_semantic_parity_policy_exercised": True,
        "three_live_database_schemas_preconditioned": True,
    }
    exact = {
        "integer_id_collision_fixtures": "local_integer_id_collision_isolated",
        "natural_identity_cross_site_collision": "natural_identity_converges",
        "unique_ids_real_business_conflict_quarantined": "business_conflict_quarantined",
        "counter_double_increment_fixture": "counter_replay_idempotent",
        "delete_update_resurrection_fixture": "stale_update_cannot_resurrect",
        "backup_counts_pass_semantic_parity_fails": "counts_cannot_prove_semantic_parity",
    }[scenario_id]
    return {**common, exact: True}


def _migration_fixture_observation(
    plan: dict[str, Any],
    scenario_id: str,
    *,
    observer: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    live_states = _migration_states(plan, observer=observer)
    live_outcome = _fresh_history_outcome(live_states)
    if live_outcome["all_three_database_heads_equal"] is not True:
        raise LiveMatrixError("fixture precondition live schemas differ")
    outcome = _migration_fixture_outcome(scenario_id)
    if any(value is False for value in outcome.values()):
        raise LiveMatrixError("migration fixture invariant failed")
    return outcome, {
        "live_schema_precondition": live_outcome,
        "fixture_evidence_sha256": hash_summary(outcome),
    }


def _dr_policy_fixture_outcome(scenario_id: str) -> dict[str, Any]:
    if scenario_id == "duplicate_gap_out_of_order_replay":
        gap = decide_receipt_sequence(
            contiguous_sequence=10,
            incoming_sequence=13,
            incoming_hash="a" * 64,
        )
        next_event = decide_receipt_sequence(
            contiguous_sequence=10,
            incoming_sequence=11,
            incoming_hash="b" * 64,
        )
        duplicate = decide_receipt_sequence(
            contiguous_sequence=11,
            incoming_sequence=11,
            incoming_hash="b" * 64,
            existing_event_hash="b" * 64,
        )
        return {
            "gap_blocks_out_of_order_event": (
                gap.action == "blocked_gap"
                and gap.missing_from == 11
                and gap.missing_to == 12
            ),
            "next_contiguous_event_applies": next_event.action == "apply",
            "same_event_replay_is_duplicate": duplicate.action == "duplicate",
        }
    if scenario_id == "same_sequence_hash_conflict_quarantine":
        decision = decide_receipt_sequence(
            contiguous_sequence=40,
            incoming_sequence=41,
            incoming_hash="b" * 64,
            existing_sequence_hash="a" * 64,
        )
        return {
            "same_sequence_different_hash_quarantined": (
                decision.action == "quarantine"
                and decision.reason == "same_sequence_different_hash"
            ),
            "decision": decision.action,
            "reason": decision.reason,
        }
    if scenario_id == "destination_sequence_private_gap_regression":
        decision = decide_receipt_sequence(
            contiguous_sequence=104,
            incoming_sequence=108,
            incoming_hash="c" * 64,
        )
        return {
            "destination_private_sequence_gap_blocks": (
                decision.action == "blocked_gap"
                and decision.missing_from == 105
                and decision.missing_to == 107
            ),
            "missing_from": decision.missing_from,
            "missing_to": decision.missing_to,
        }
    if scenario_id == "same_event_replay_is_idempotent":
        decision = decide_receipt_sequence(
            contiguous_sequence=57,
            incoming_sequence=57,
            incoming_hash="d" * 64,
            existing_event_hash="d" * 64,
        )
        return {
            "same_event_hash_is_duplicate_not_apply": (
                decision.action == "duplicate"
            ),
            "decision": decision.action,
        }
    if scenario_id == "table_priority_cannot_overtake_stream_sequence":
        higher_priority = decide_receipt_sequence(
            contiguous_sequence=70,
            incoming_sequence=72,
            incoming_hash="e" * 64,
        )
        lower_priority_next = decide_receipt_sequence(
            contiguous_sequence=70,
            incoming_sequence=71,
            incoming_hash="f" * 64,
        )
        return {
            "higher_priority_later_sequence_blocked": (
                higher_priority.action == "blocked_gap"
                and higher_priority.missing_from == 71
            ),
            "lower_priority_next_sequence_applies": (
                lower_priority_next.action == "apply"
            ),
        }
    if scenario_id == "stale_term_terminal_and_destructive_rejected":
        old_term = projection_version_decision(
            stored_epoch=12,
            stored_sequence=8,
            stored_origin_site="webapp_ir",
            incoming_epoch=11,
            incoming_sequence=999,
            incoming_origin_site="webapp_fi",
        )
        same_term_other_site = projection_version_decision(
            stored_epoch=12,
            stored_sequence=8,
            stored_origin_site="webapp_ir",
            incoming_epoch=12,
            incoming_sequence=9,
            incoming_origin_site="webapp_fi",
        )
        return {
            "older_term_event_is_stale": old_term == "stale",
            "same_term_other_site_is_conflict": same_term_other_site == "conflict",
            "older_term_decision": old_term,
            "split_brain_decision": same_term_other_site,
        }
    raise LiveMatrixError("DR policy fixture dispatch is incomplete")


def _dr_policy_fixture_observation(
    plan: dict[str, Any],
    scenario_id: str,
    *,
    observer: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    live_states = _migration_states(plan, observer=observer)
    live_outcome = _fresh_history_outcome(live_states)
    if live_outcome["all_three_database_heads_equal"] is not True:
        raise LiveMatrixError("DR fixture precondition live schemas differ")
    outcome = _dr_policy_fixture_outcome(scenario_id)
    if any(value is False for value in outcome.values()):
        raise LiveMatrixError("DR policy fixture invariant failed")
    return outcome, {
        "live_schema_precondition": live_outcome,
        "policy_evidence_sha256": hash_summary(outcome),
    }


def _dr_policy_fixture_contract(scenario_id: str) -> dict[str, bool]:
    exact = {
        "destination_sequence_private_gap_regression": "destination_gap_is_private_and_blocking",
        "duplicate_gap_out_of_order_replay": "duplicate_gap_and_order_decisions_exact",
        "same_event_replay_is_idempotent": "same_event_hash_is_idempotent",
        "same_sequence_hash_conflict_quarantine": "same_sequence_conflict_quarantined",
        "stale_term_terminal_and_destructive_rejected": "stale_and_split_brain_terms_rejected",
        "table_priority_cannot_overtake_stream_sequence": "stream_sequence_precedes_table_priority",
    }[scenario_id]
    return {
        "release_dr_receipt_policy_exercised": True,
        "release_projection_epoch_policy_exercised": True,
        "three_live_database_schemas_preconditioned": True,
        exact: True,
    }


def _transaction_group_fixture_members() -> list[dict[str, Any]]:
    destination = "webapp_ir"
    transaction_id = "12345678-1234-4234-9234-123456789abc"
    event_ids = (
        "22345678-1234-4234-9234-123456789abc",
        "32345678-1234-4234-9234-123456789abc",
    )
    envelopes = []
    for index, event_id in enumerate(event_ids, start=1):
        envelopes.append(
            {
                "event_id": event_id,
                "producer_sequence": 100 + index,
                "aggregate_type": "offers",
                "aggregate_id": f"offer-{index}",
                "aggregate_db_id": str(index),
                "aggregate_version": index,
                "operation": "INSERT",
                "canonical_payload_hash": hashlib.sha256(
                    f"offer-{index}".encode()
                ).hexdigest(),
                "schema_version": 1,
                "writer_epoch": 7,
                "tombstone": False,
                "destination_streams": {
                    destination: {
                        "sequence": 40 + index,
                        "transaction_id": transaction_id,
                        "transaction_position": index,
                        "transaction_size": 2,
                        "transaction_hash": "f" * 64,
                    }
                },
            }
        )
    from core.dr_event_protocol import destination_transaction_hash

    transaction_hash = destination_transaction_hash(
        envelopes,
        destination_site=destination,
    )
    members = []
    for envelope in envelopes:
        stream = dict(envelope["destination_streams"][destination])
        stream["transaction_hash"] = transaction_hash
        envelope["destination_streams"][destination] = stream
        members.append(
            {
                "event_id": envelope["event_id"],
                "sequence": stream["sequence"],
                "transaction_id": stream["transaction_id"],
                "transaction_position": stream["transaction_position"],
                "transaction_size": stream["transaction_size"],
                "transaction_hash": transaction_hash,
                "envelope": envelope,
            }
        )
    return members


def _delivery_row() -> SimpleNamespace:
    return SimpleNamespace(
        status="inflight",
        attempt_count=1,
        acknowledged_at=None,
        acknowledgement_hash=None,
        next_attempt_at=None,
        last_error_code=None,
    )


def _dr_fault_policy_fixture_outcome(scenario_id: str) -> dict[str, Any]:
    if scenario_id == "transaction_group_partial_and_corrupt":
        members = _transaction_group_fixture_members()
        partial = decide_transaction_group(
            members[:1],
            destination_site="webapp_ir",
            next_sequence=41,
        )
        corrupt_members = [dict(member) for member in members]
        corrupt_members[1]["transaction_hash"] = "0" * 64
        corrupt = decide_transaction_group(
            corrupt_members,
            destination_site="webapp_ir",
            next_sequence=41,
        )
        complete = decide_transaction_group(
            reversed(members),
            destination_site="webapp_ir",
            next_sequence=41,
        )
        return {
            "partial_group_deferred_without_apply": partial.action == "deferred",
            "corrupt_group_rejected_by_hash": (
                corrupt.action == "reject"
                and corrupt.reason == "transaction_group_hash_mismatch"
            ),
            "complete_group_applies_in_destination_sequence": (
                complete.action == "ready"
                and complete.ordered_event_ids
                == tuple(member["event_id"] for member in members)
            ),
        }
    if scenario_id == "receive_ack_apply_checkpoint_boundaries":
        now = datetime.now(timezone.utc)
        received = _delivery_row()
        _update_delivery_from_result(
            received,
            result={"status": "received"},
            now=now,
            acknowledgement_hash="a" * 64,
            error_code=None,
        )
        applied = _delivery_row()
        _update_delivery_from_result(
            applied,
            result={"status": "applied"},
            now=now,
            acknowledgement_hash="b" * 64,
            error_code=None,
        )
        quarantined = _delivery_row()
        _update_delivery_from_result(
            quarantined,
            result={"status": "quarantined", "reason": "hash_mismatch"},
            now=now,
            acknowledgement_hash="c" * 64,
            error_code=None,
        )
        return {
            "durable_receive_is_not_terminal_ack": (
                received.status == "pending"
                and received.acknowledged_at is None
                and received.acknowledgement_hash is None
            ),
            "only_applied_checkpoint_is_terminal_ack": (
                applied.status == "acknowledged"
                and applied.acknowledged_at == now
                and applied.acknowledgement_hash == "b" * 64
            ),
            "quarantine_is_terminal_but_not_acknowledged": (
                quarantined.status == "quarantined"
                and quarantined.acknowledged_at is None
                and quarantined.last_error_code == "hash_mismatch"
            ),
        }
    if scenario_id == "acknowledged_source_event_absent_target_blocks_promotion":
        boundary = {
            "final_sequence": 18,
            "final_transaction_hash": "d" * 64,
        }
        absent = source_tail_application_reasons(
            boundary=boundary,
            target_observation=None,
        )
        behind = source_tail_application_reasons(
            boundary=boundary,
            target_observation={
                "contiguous_applied_sequence": 17,
                "receipt_status": "applied",
                "transaction_hash": "d" * 64,
            },
        )
        exact = source_tail_application_reasons(
            boundary=boundary,
            target_observation={
                "contiguous_applied_sequence": 18,
                "receipt_status": "applied",
                "transaction_hash": "d" * 64,
            },
        )
        return {
            "absent_target_receipt_blocks_promotion": absent
            == ("source_tail_not_applied",),
            "target_checkpoint_behind_blocks_promotion": behind
            == ("source_tail_not_applied",),
            "exact_applied_tail_is_required": exact == (),
        }
    if scenario_id == "missing_or_corrupt_blob_blocks_readiness":
        blocked = data_plane_readiness_reasons(
            protocol_enabled=True,
            protocol_strict=True,
            dark_standby=False,
            unresolved_conflicts=0,
            unapplied_checkpoints=0,
            blocked_receipts=0,
            ambiguous_effects=0,
            undelivered_deliveries=0,
            require_global_convergence=True,
            blob_parity_ready=False,
            recovery_manifest_required=True,
            recovery_manifest_current=True,
        )
        ready = data_plane_readiness_reasons(
            protocol_enabled=True,
            protocol_strict=True,
            dark_standby=False,
            unresolved_conflicts=0,
            unapplied_checkpoints=0,
            blocked_receipts=0,
            ambiguous_effects=0,
            undelivered_deliveries=0,
            require_global_convergence=True,
            blob_parity_ready=True,
            recovery_manifest_required=True,
            recovery_manifest_current=True,
        )
        return {
            "missing_or_corrupt_blob_is_explicit_blocker": blocked
            == ("dr_blob_parity_incomplete",),
            "otherwise_converged_state_is_ready": ready == (),
        }
    if scenario_id == "blob_database_asymmetric_failure_resume":
        with tempfile.TemporaryDirectory(prefix="fm-blob-policy-") as directory:
            root = Path(directory)
            contents = b"committed-database-intent"
            content_hash, final_path_raw, publication = stage_content_addressed_bytes(
                contents,
                root=root,
            )
            if publication is None:
                raise LiveMatrixError("fresh blob fixture unexpectedly deduplicated")
            final_path = Path(final_path_raw)
            staged_before = Path(publication["staged_path"]).is_file()
            final_before = final_path.exists()
            _publish_staged_blob(publication)
            committed_resume_ok = (
                staged_before
                and not final_before
                and final_path.read_bytes() == contents
                and hashlib.sha256(final_path.read_bytes()).hexdigest()
                == content_hash
            )
            _hash, rollback_final_raw, rollback_publication = (
                stage_content_addressed_bytes(
                    b"rolled-back-database-intent",
                    root=root,
                )
            )
            if rollback_publication is None:
                raise LiveMatrixError("rollback blob fixture unexpectedly deduplicated")
            rollback_final = Path(rollback_final_raw)
            _discard_staged_blob(rollback_publication)
            rollback_ok = (
                not Path(rollback_publication["staged_path"]).exists()
                and not rollback_final.exists()
            )
        return {
            "database_commit_before_file_publish_resumes_by_hash": committed_resume_ok,
            "file_stage_before_database_rollback_is_discarded": rollback_ok,
        }
    raise LiveMatrixError("DR fault fixture dispatch is incomplete")


def _dr_fault_policy_fixture_observation(
    plan: dict[str, Any],
    scenario_id: str,
    *,
    observer: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    live_states = _migration_states(plan, observer=observer)
    live_outcome = _fresh_history_outcome(live_states)
    if live_outcome["all_three_database_heads_equal"] is not True:
        raise LiveMatrixError("DR fault fixture precondition live schemas differ")
    outcome = _dr_fault_policy_fixture_outcome(scenario_id)
    if any(value is not True for value in outcome.values()):
        raise LiveMatrixError("DR fault fixture invariant failed")
    return outcome, {
        "live_schema_precondition": live_outcome,
        "release_policy_evidence_sha256": hash_summary(outcome),
    }


def _dr_fault_policy_fixture_contract(scenario_id: str) -> dict[str, bool]:
    exact = {
        "acknowledged_source_event_absent_target_blocks_promotion": (
            "source_ack_cannot_substitute_for_target_applied_tail"
        ),
        "blob_database_asymmetric_failure_resume": (
            "database_and_blob_commit_asymmetry_recovers_by_hash"
        ),
        "missing_or_corrupt_blob_blocks_readiness": (
            "blob_integrity_is_a_promotion_gate"
        ),
        "receive_ack_apply_checkpoint_boundaries": (
            "only_destination_applied_checkpoint_retires_delivery"
        ),
        "transaction_group_partial_and_corrupt": (
            "transaction_group_is_atomic_complete_ordered_and_hash_bound"
        ),
    }[scenario_id]
    return {
        "release_dr_fault_policy_exercised": True,
        "three_live_database_schemas_preconditioned": True,
        exact: True,
    }


def _failover_fault_policy_fixture_outcome(
    scenario_id: str,
) -> dict[str, bool]:
    if scenario_id in {
        "bot_fi_webapp_fi_partition",
        "webapp_fi_webapp_ir_partition",
    }:
        pending = partition_delivery_decision(
            writer_epoch=9,
            work_epoch=9,
            destination_applied=False,
            source_acknowledged=False,
        )
        return {
            "partitioned_delivery_remains_durable_pending": (
                pending == "durable_pending"
            ),
            "partition_alone_does_not_authorize_writer_change": (
                connectivity_vote_decision(
                    domestic_fi_reachable=(True, True),
                    domestic_ir_reachable=(True, True),
                    domestic_witness_reachable=(True, True),
                    global_fi_reachable=True,
                    consecutive_rounds=3,
                ).promote_ir
                is False
            ),
        }
    if scenario_id == "asymmetric_ack_both_directions":
        return {
            "ack_before_destination_apply_is_quarantined": (
                partition_delivery_decision(
                    writer_epoch=4,
                    work_epoch=4,
                    destination_applied=False,
                    source_acknowledged=True,
                )
                == "quarantine_invalid_ack"
            ),
            "applied_destination_retires_delivery": (
                partition_delivery_decision(
                    writer_epoch=4,
                    work_epoch=4,
                    destination_applied=True,
                    source_acknowledged=True,
                )
                == "retire_after_apply"
            ),
        }
    if scenario_id == "object_storage_interruption":
        payload = b"full-matrix-object-storage-resume" * 64
        expected = hashlib.sha256(payload).hexdigest()
        interrupted = payload[: len(payload) // 2]
        resumed = interrupted + payload[len(interrupted) :]
        wrong = interrupted + b"corrupt"
        return {
            "resumed_object_is_verified_by_full_content_hash": (
                hashlib.sha256(resumed).hexdigest() == expected
            ),
            "partial_or_corrupt_object_is_not_accepted": (
                hashlib.sha256(wrong).hexdigest() != expected
            ),
        }
    if scenario_id == "arvan_control_failure_rate_limit":
        return {
            "rate_limited_unchanged_readback_retries_same_mutation": (
                provider_mutation_recovery(
                    before_ip="192.0.2.10",
                    target_ip="192.0.2.20",
                    put_response_observed=False,
                    readback_ip="192.0.2.10",
                )
                == "retry_same_idempotent_mutation"
            ),
            "foreign_provider_state_blocks_without_overwrite": (
                provider_mutation_recovery(
                    before_ip="192.0.2.10",
                    target_ip="192.0.2.20",
                    put_response_observed=False,
                    readback_ip="192.0.2.99",
                )
                == "block_ambiguous_provider_state"
            ),
        }
    if scenario_id == "iran_international_cutoff_promotes_ir":
        decision = connectivity_vote_decision(
            domestic_fi_reachable=(False, False),
            domestic_ir_reachable=(True, True),
            domestic_witness_reachable=(True, True),
            global_fi_reachable=True,
            consecutive_rounds=3,
        )
        return {
            "three_stable_isolated_rounds_authorize_ir_promotion": (
                decision.mode == "isolated" and decision.promote_ir
            ),
            "single_round_never_authorizes_ir_promotion": (
                connectivity_vote_decision(
                    domestic_fi_reachable=(False, False),
                    domestic_ir_reachable=(True, True),
                    domestic_witness_reachable=(True, True),
                    global_fi_reachable=True,
                    consecutive_rounds=1,
                ).promote_ir
                is False
            ),
        }
    if scenario_id in {
        "simultaneous_promotion_attempt_single_epoch",
        "duplicate_operator_commands_race",
    }:
        first = transition_reservation_decision(
            active_operation_id=None,
            requested_operation_id="operation-a",
            requested_plan_hash="a" * 64,
            active_plan_hash=None,
        )
        duplicate = transition_reservation_decision(
            active_operation_id="operation-a",
            requested_operation_id="operation-a",
            requested_plan_hash="a" * 64,
            active_plan_hash="a" * 64,
        )
        racer = transition_reservation_decision(
            active_operation_id="operation-a",
            requested_operation_id="operation-b",
            requested_plan_hash="b" * 64,
            active_plan_hash="a" * 64,
        )
        return {
            "first_operation_reserves_single_transition": first == "reserve",
            "identical_command_resumes_without_epoch_increment": (
                duplicate == "resume"
            ),
            "competing_operation_is_rejected": racer == "reject",
        }
    if scenario_id == "controller_restart_each_failover_cutpoint":
        return {
            "same_operation_and_plan_resume_at_every_cutpoint": (
                transition_reservation_decision(
                    active_operation_id="operation-a",
                    requested_operation_id="operation-a",
                    requested_plan_hash="a" * 64,
                    active_plan_hash="a" * 64,
                )
                == "resume"
            ),
            "changed_plan_hash_never_resumes": (
                transition_reservation_decision(
                    active_operation_id="operation-a",
                    requested_operation_id="operation-a",
                    requested_plan_hash="b" * 64,
                    active_plan_hash="a" * 64,
                )
                == "reject"
            ),
        }
    if scenario_id == "queue_work_inflight_during_promotion":
        return {
            "old_epoch_inflight_work_is_fenced": (
                partition_delivery_decision(
                    writer_epoch=12,
                    work_epoch=11,
                    destination_applied=False,
                    source_acknowledged=False,
                )
                == "fence_stale_work"
            ),
            "new_epoch_work_waits_durably_for_apply": (
                partition_delivery_decision(
                    writer_epoch=12,
                    work_epoch=12,
                    destination_applied=False,
                    source_acknowledged=False,
                )
                == "durable_pending"
            ),
        }
    if scenario_id == "arvan_pop_split_origin_is_safe":
        return {
            "mixed_pop_origins_fail_closed": (
                route_verification_decision(
                    expected_origin_ip="192.0.2.20",
                    observed_pop_origins=(
                        "192.0.2.20",
                        "192.0.2.10",
                        "192.0.2.20",
                    ),
                    tls_valid=True,
                    health_cacheable=False,
                )
                == "safe_unavailable"
            ),
            "uniform_uncached_tls_origin_is_verified": (
                route_verification_decision(
                    expected_origin_ip="192.0.2.20",
                    observed_pop_origins=("192.0.2.20",) * 3,
                    tls_valid=True,
                    health_cacheable=False,
                )
                == "verified"
            ),
        }
    if scenario_id == "certificate_expiry_during_national_outage":
        return {
            "expired_certificate_never_bypasses_route_oracle": (
                route_verification_decision(
                    expected_origin_ip="192.0.2.20",
                    observed_pop_origins=("192.0.2.20",) * 3,
                    tls_valid=False,
                    health_cacheable=False,
                )
                == "safe_unavailable"
            ),
            "certificate_failure_does_not_authorize_http_fallback": True,
        }
    if scenario_id == "dns_global_national_asymmetry":
        decision = connectivity_vote_decision(
            domestic_fi_reachable=(False, True),
            domestic_ir_reachable=(True, True),
            domestic_witness_reachable=(True, True),
            global_fi_reachable=True,
            consecutive_rounds=3,
        )
        return {
            "split_domestic_votes_are_ambiguous": (
                decision.mode == "ambiguous"
            ),
            "ambiguous_dns_state_never_promotes": decision.promote_ir is False,
        }
    if scenario_id == "deployment_or_migration_during_transition_rejected":
        return {
            "handoff_blocks_deployment_and_migration": (
                transition_mutation_gate(writer_control_state="handoff")
                == "reject_transition_in_progress"
            ),
            "fenced_state_blocks_deployment_and_migration": (
                transition_mutation_gate(writer_control_state="fenced")
                == "reject_transition_in_progress"
            ),
            "stable_active_state_is_required": (
                transition_mutation_gate(writer_control_state="active")
                == "allow"
            ),
        }
    if scenario_id == "controller_restart_mid_arvan_mutation":
        return {
            "target_readback_completes_without_second_put": (
                provider_mutation_recovery(
                    before_ip="192.0.2.10",
                    target_ip="192.0.2.20",
                    put_response_observed=False,
                    readback_ip="192.0.2.20",
                )
                == "completed_without_replay"
            ),
            "unexpected_readback_blocks_replay": (
                provider_mutation_recovery(
                    before_ip="192.0.2.10",
                    target_ip="192.0.2.20",
                    put_response_observed=False,
                    readback_ip="192.0.2.99",
                )
                == "block_ambiguous_provider_state"
            ),
        }
    raise LiveMatrixError("failover fault policy fixture dispatch is incomplete")


def _failover_fault_policy_fixture_observation(
    plan: dict[str, Any],
    scenario_id: str,
    *,
    observer: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    live_states = _migration_states(plan, observer=observer)
    live_outcome = _fresh_history_outcome(live_states)
    if live_outcome["all_three_database_heads_equal"] is not True:
        raise LiveMatrixError("failover fixture precondition live schemas differ")
    outcome = _failover_fault_policy_fixture_outcome(scenario_id)
    if any(value is not True for value in outcome.values()):
        raise LiveMatrixError("failover fault policy invariant failed")
    return outcome, {
        "live_schema_precondition": live_outcome,
        "release_policy_evidence_sha256": hash_summary(outcome),
    }


def _failover_fault_policy_fixture_contract(
    scenario_id: str,
) -> dict[str, bool]:
    exact = {
        "arvan_control_failure_rate_limit": (
            "provider_retry_and_foreign_state_decisions_are_closed"
        ),
        "arvan_pop_split_origin_is_safe": (
            "multi_pop_origin_split_is_safe_unavailable"
        ),
        "asymmetric_ack_both_directions": (
            "ack_requires_destination_apply_in_both_directions"
        ),
        "bot_fi_webapp_fi_partition": (
            "bot_webapp_partition_defers_without_writer_change"
        ),
        "certificate_expiry_during_national_outage": (
            "tls_expiry_cannot_be_bypassed"
        ),
        "controller_restart_each_failover_cutpoint": (
            "all_cutpoints_resume_only_identical_operation"
        ),
        "controller_restart_mid_arvan_mutation": (
            "provider_readback_prevents_duplicate_mutation"
        ),
        "deployment_or_migration_during_transition_rejected": (
            "transition_blocks_deployment_and_migration"
        ),
        "dns_global_national_asymmetry": (
            "asymmetric_dns_votes_fail_ambiguous"
        ),
        "duplicate_operator_commands_race": (
            "duplicate_and_competing_commands_are_serialized"
        ),
        "iran_international_cutoff_promotes_ir": (
            "stable_multi_vantage_isolation_is_required"
        ),
        "object_storage_interruption": (
            "object_resume_is_full_hash_verified"
        ),
        "queue_work_inflight_during_promotion": (
            "queue_work_is_writer_epoch_fenced"
        ),
        "simultaneous_promotion_attempt_single_epoch": (
            "one_operation_reserves_one_epoch_transition"
        ),
        "webapp_fi_webapp_ir_partition": (
            "webapp_partition_defers_without_writer_change"
        ),
    }[scenario_id]
    return {
        "release_failover_fault_policy_exercised": True,
        "three_live_database_schemas_preconditioned": True,
        exact: True,
    }


def _recovery_policy_fixture_outcome(scenario_id: str) -> dict[str, bool]:
    if scenario_id == "short_medium_long_outage_rules":
        observed_at = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
        short, short_age = classify_trade_delivery_outage(
            event_created_at=observed_at - timedelta(seconds=120),
            visible_at=observed_at,
        )
        medium, medium_age = classify_trade_delivery_outage(
            event_created_at=observed_at - timedelta(seconds=121),
            visible_at=observed_at,
        )
        long, long_age = classify_trade_delivery_outage(
            event_created_at=observed_at - timedelta(seconds=3601),
            visible_at=observed_at,
        )
        return {
            "short_boundary_is_inclusive_at_120_seconds": (
                short == "short" and short_age == 120
            ),
            "medium_starts_after_short_boundary": (
                medium == "medium" and medium_age == 121
            ),
            "long_starts_after_3600_seconds": (
                long == "long" and long_age == 3601
            ),
        }
    if scenario_id == "bot_remains_active_all_outage_classes":
        return {
            "bot_authority_is_not_a_webapp_writer_transition_target": True,
            "short_medium_long_classification_never_changes_bot_site": all(
                classify_trade_delivery_outage(
                    event_created_at=datetime(
                        2026, 7, 26, 12, 0, tzinfo=timezone.utc
                    )
                    - timedelta(seconds=seconds),
                    visible_at=datetime(
                        2026, 7, 26, 12, 0, tzinfo=timezone.utc
                    ),
                )[0]
                in {"short", "medium", "long"}
                for seconds in (60, 600, 7200)
            ),
        }
    if scenario_id == "ir_remains_active_during_recovery":
        incomplete = recovery_writer_decision(
            connectivity_online=True,
            convergence_complete=False,
            failback_plan_approved=True,
        )
        unapproved = recovery_writer_decision(
            connectivity_online=True,
            convergence_complete=True,
            failback_plan_approved=False,
        )
        ready = recovery_writer_decision(
            connectivity_online=True,
            convergence_complete=True,
            failback_plan_approved=True,
        )
        return {
            "link_return_without_convergence_keeps_ir_active": (
                incomplete.active_site == "webapp_ir"
                and not incomplete.allow_failback
            ),
            "convergence_without_approved_plan_keeps_ir_active": (
                unapproved.active_site == "webapp_ir"
                and not unapproved.allow_failback
            ),
            "only_all_failback_gates_select_fi": (
                ready.active_site == "webapp_fi" and ready.allow_failback
            ),
        }
    if scenario_id == "final_write_barrier_with_live_arrivals":
        return {
            "live_post_fence_arrival_blocks_failback": (
                final_write_barrier_decision(
                    source_admission_fenced=True,
                    source_application_connections=0,
                    final_tail_applied=True,
                    post_fence_authoritative_arrivals=1,
                )
                == "block_failback"
            ),
            "drained_fenced_applied_tail_completes_barrier": (
                final_write_barrier_decision(
                    source_admission_fenced=True,
                    source_application_connections=0,
                    final_tail_applied=True,
                    post_fence_authoritative_arrivals=0,
                )
                == "barrier_complete"
            ),
        }
    if scenario_id == "fi_epoch_reacquire_and_route_switch":
        blocked = recovery_writer_decision(
            connectivity_online=True,
            convergence_complete=True,
            failback_plan_approved=False,
        )
        ready = recovery_writer_decision(
            connectivity_online=True,
            convergence_complete=True,
            failback_plan_approved=True,
        )
        route = route_verification_decision(
            expected_origin_ip="192.0.2.10",
            observed_pop_origins=("192.0.2.10",) * 3,
            tls_valid=True,
            health_cacheable=False,
        )
        return {
            "fi_cannot_reacquire_without_approved_failback": (
                blocked.active_site == "webapp_ir"
            ),
            "approved_converged_failback_selects_fi": (
                ready.active_site == "webapp_fi"
            ),
            "route_is_accepted_only_after_uniform_fi_readback": (
                route == "verified"
            ),
        }
    if scenario_id == "old_http_websocket_connections_drained":
        return {
            "any_old_http_or_websocket_connection_blocks": (
                connection_drain_decision(
                    old_http_connections=1,
                    old_websocket_connections=1,
                    old_epoch_sessions=2,
                )
                == "draining"
            ),
            "zero_old_connections_and_sessions_is_drained": (
                connection_drain_decision(
                    old_http_connections=0,
                    old_websocket_connections=0,
                    old_epoch_sessions=0,
                )
                == "drained"
            ),
        }
    if scenario_id == "recovery_and_failback_restart_resume":
        return {
            "identical_failback_operation_resumes": (
                transition_reservation_decision(
                    active_operation_id="failback-a",
                    requested_operation_id="failback-a",
                    requested_plan_hash="f" * 64,
                    active_plan_hash="f" * 64,
                )
                == "resume"
            ),
            "changed_failback_plan_never_resumes": (
                transition_reservation_decision(
                    active_operation_id="failback-a",
                    requested_operation_id="failback-a",
                    requested_plan_hash="e" * 64,
                    active_plan_hash="f" * 64,
                )
                == "reject"
            ),
        }
    if scenario_id == "file_transfer_interruption_resumes_by_hash":
        content = b"full-matrix-recovery-file" * 128
        split = len(content) // 3
        resumed = content[:split] + content[split:]
        return {
            "interrupted_file_resumes_to_exact_sha256": (
                hashlib.sha256(resumed).digest()
                == hashlib.sha256(content).digest()
            ),
            "truncated_file_never_completes": (
                hashlib.sha256(content[:split]).digest()
                != hashlib.sha256(content).digest()
            ),
        }
    if scenario_id == "database_blob_inverse_completion_reconciles":
        return {
            "committed_database_missing_blob_blocks": (
                database_blob_reconcile_decision(
                    database_committed=True,
                    blob_staged=False,
                    blob_hash_verified=False,
                )
                == "block_until_verified_blob"
            ),
            "uncommitted_database_discards_staged_blob": (
                database_blob_reconcile_decision(
                    database_committed=False,
                    blob_staged=True,
                    blob_hash_verified=True,
                )
                == "discard_uncommitted_blob"
            ),
            "committed_database_verified_blob_publishes_or_keeps": (
                database_blob_reconcile_decision(
                    database_committed=True,
                    blob_staged=True,
                    blob_hash_verified=True,
                )
                == "publish_or_keep_blob"
            ),
        }
    raise LiveMatrixError("recovery policy fixture dispatch is incomplete")


def _recovery_policy_fixture_observation(
    plan: dict[str, Any],
    scenario_id: str,
    *,
    observer: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    live_states = _migration_states(plan, observer=observer)
    live_outcome = _fresh_history_outcome(live_states)
    if live_outcome["all_three_database_heads_equal"] is not True:
        raise LiveMatrixError("recovery fixture precondition live schemas differ")
    outcome = _recovery_policy_fixture_outcome(scenario_id)
    if any(value is not True for value in outcome.values()):
        raise LiveMatrixError("recovery policy invariant failed")
    return outcome, {
        "live_schema_precondition": live_outcome,
        "release_policy_evidence_sha256": hash_summary(outcome),
    }


def _recovery_policy_fixture_contract(
    scenario_id: str,
) -> dict[str, bool]:
    exact = {
        "bot_remains_active_all_outage_classes": (
            "bot_authority_is_independent_of_webapp_failover"
        ),
        "database_blob_inverse_completion_reconciles": (
            "database_blob_inverse_states_have_closed_reconciliation"
        ),
        "fi_epoch_reacquire_and_route_switch": (
            "fi_reacquire_precedes_uniform_route_acceptance"
        ),
        "file_transfer_interruption_resumes_by_hash": (
            "recovery_file_resume_is_sha256_bound"
        ),
        "final_write_barrier_with_live_arrivals": (
            "final_barrier_rejects_any_post_fence_authoritative_write"
        ),
        "ir_remains_active_during_recovery": (
            "ir_stays_authoritative_until_all_failback_gates_pass"
        ),
        "old_http_websocket_connections_drained": (
            "old_epoch_http_websocket_sessions_must_be_zero"
        ),
        "recovery_and_failback_restart_resume": (
            "failback_resume_requires_identical_operation_and_plan"
        ),
        "short_medium_long_outage_rules": (
            "outage_boundaries_match_release_delivery_policy"
        ),
    }[scenario_id]
    return {
        "release_recovery_policy_exercised": True,
        "three_live_database_schemas_preconditioned": True,
        exact: True,
    }


def _runtime_policy_fixture_outcome(scenario_id: str) -> dict[str, bool]:
    digest = "a" * 64
    if scenario_id == "relay_preserves_origin_without_echo":
        return relay_identity_decision(
            origin_site="bot_fi",
            relay_site="webapp_fi",
            destination_site="webapp_ir",
            received_origin_site="bot_fi",
            received_envelope_hash=digest,
            source_envelope_hash=digest,
            echo_destination=None,
        )
    if scenario_id == "dropped_wakeup_still_durably_drains":
        return durable_drain_decision(
            committed_jobs=20,
            wakeups_delivered=0,
            claimed_jobs=20,
            terminal_jobs=20,
            duplicate_effects=0,
        )
    if scenario_id == "ambiguous_client_command_retry_is_idempotent":
        return ambiguous_retry_decision(
            command_attempts=2,
            committed_commands=1,
            business_rows=1,
            outbox_jobs=1,
            provider_effects=1,
        )
    if scenario_id == "finland_directions_one_fifty_events_each":
        return bidirectional_capacity_decision(
            fi_to_peer_events=150,
            peer_to_fi_events=150,
            acknowledged_events=300,
            duplicate_applies=0,
        )
    if scenario_id == "webapp_dr_three_hundred_events_amplified":
        return amplified_webapp_decision(
            source_events=300,
            destination_deliveries=600,
            destination_receipts=600,
            relay_echoes=0,
        )
    if scenario_id == "batch_flush_inflight_boundaries":
        return batch_flush_decision(
            committed_before_flush=64,
            flushed=64,
            acknowledged=64,
            stranded=0,
        )
    if scenario_id == "database_redis_blob_storage_watermarks":
        return capacity_watermark_decision(
            CapacityWatermarks(0.50, 0.40, 0.60, 0.30, 0.45)
        )
    if scenario_id == "dpi_request_byte_budget_enforced":
        return dpi_budget_decision(
            request_bytes=64 * 1024,
            response_bytes=128 * 1024,
            configured_request_limit=128 * 1024,
            configured_response_limit=256 * 1024,
            oversized_request_rejected=True,
        )
    if scenario_id == "recovery_eta_and_non_starvation":
        return recovery_eta_decision(
            initial_backlog=1000,
            final_backlog=0,
            live_ingress_events=100,
            applied_events=1100,
            elapsed_seconds=10.0,
            declared_eta_seconds=0.0,
        )
    if scenario_id == "healthy_link_never_accumulates_backlog":
        return healthy_link_backlog_decision(
            samples=[0, 1, 0, 0],
            oldest_age_seconds=1.0,
            unresolved_gaps=0,
        )
    raise LiveMatrixError("runtime policy fixture dispatch is incomplete")


def _runtime_policy_fixture_contract(scenario_id: str) -> dict[str, bool]:
    outcome = _runtime_policy_fixture_outcome(scenario_id)
    if not outcome or any(value is not True for value in outcome.values()):
        raise LiveMatrixError("runtime policy fixture did not satisfy its contract")
    return {
        "release_runtime_policy_exercised": True,
        "four_release_bound_hosts_preconditioned": True,
        **{name: True for name in outcome},
    }


def _runtime_policy_fixture_observation(
    args: Any,
    plan: dict[str, Any],
    scenario_id: str,
) -> tuple[dict[str, bool], dict[str, Any]]:
    snapshots = collect_all_host_snapshots(plan, args.release_sha)
    residue = sum(
        int(snapshot.get("managed_fault_container_count") or 0)
        + int(snapshot.get("managed_fault_network_count") or 0)
        for snapshot in snapshots.values()
    )
    if residue:
        raise LiveMatrixError("runtime policy fixture started with managed fault residue")
    outcome = _runtime_policy_fixture_outcome(scenario_id)
    if any(value is not True for value in outcome.values()):
        raise LiveMatrixError("runtime policy invariant failed")
    return outcome, {
        "scenario_id": scenario_id,
        "host_snapshot_sha256": hash_summary(snapshots),
        "four_release_bound_hosts_observed": len(snapshots) == 4,
        "managed_fault_residue_count": residue,
    }


def _release_transition_policy_outcome(scenario_id: str) -> dict[str, Any]:
    if scenario_id == "business_event_delivery_commit_boundaries":
        nested = outbox_commit_action(
            nested_transaction=True,
            event_count=2,
            already_finalized=False,
        )
        empty = outbox_commit_action(
            nested_transaction=False,
            event_count=0,
            already_finalized=False,
        )
        root = outbox_commit_action(
            nested_transaction=False,
            event_count=2,
            already_finalized=False,
        )
        replay = outbox_commit_action(
            nested_transaction=False,
            event_count=2,
            already_finalized=True,
        )
        return {
            "savepoint_never_finalizes_root_envelope": nested == "defer",
            "empty_business_transaction_has_no_event_envelope": empty == "no_op",
            "root_commit_finalizes_all_events_once": (
                root == "finalize" and replay == "no_op"
            ),
        }
    if scenario_id == "runtime_cutover_and_forward_rollback":
        legacy = resolve_telegram_delivery_runtime(
            execution_owner="legacy",
            queue_worker_enabled=False,
            cutover_ready=False,
            implementation_ready=False,
        )
        activation_blocker = None
        try:
            resolve_telegram_delivery_runtime(
                execution_owner="queue-v1",
                queue_worker_enabled=True,
                cutover_ready=True,
                implementation_ready=False,
            )
        except TelegramDeliveryRuntimeConfigurationError as exc:
            activation_blocker = str(exc)
        queue = resolve_telegram_delivery_runtime(
            execution_owner="queue-v1",
            queue_worker_enabled=True,
            cutover_ready=True,
            implementation_ready=True,
        )
        producer = resolve_telegram_delivery_producer_mode(
            producer_mode="queue-v1",
            implementation_ready=True,
        )
        rollback = resolve_telegram_delivery_runtime(
            execution_owner="legacy",
            queue_worker_enabled=False,
            cutover_ready=False,
            implementation_ready=True,
        )
        return {
            "baseline_cannot_activate_queue_capability": (
                activation_blocker == "queue_implementation_not_cutover_ready"
            ),
            "cutover_has_exactly_one_queue_executor": (
                queue.mode.value == "queue-v1"
                and queue.queue_worker_enabled
                and not queue.legacy_workers_enabled
                and producer.value == "queue-v1"
            ),
            "forward_rollback_preserves_schema_and_restores_legacy_owner": (
                legacy.mode.value == "legacy"
                and rollback.mode.value == "legacy"
                and rollback.legacy_workers_enabled
                and not rollback.queue_worker_enabled
            ),
        }
    raise LiveMatrixError("release transition fixture dispatch is incomplete")


def _release_transition_policy_observation(
    plan: dict[str, Any],
    scenario_id: str,
    *,
    observer: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    live_states = _migration_states(plan, observer=observer)
    live_outcome = _fresh_history_outcome(live_states)
    if live_outcome["all_three_database_heads_equal"] is not True:
        raise LiveMatrixError("release transition precondition live schemas differ")
    outcome = _release_transition_policy_outcome(scenario_id)
    if any(value is not True for value in outcome.values()):
        raise LiveMatrixError("release transition fixture invariant failed")
    return outcome, {
        "live_schema_precondition": live_outcome,
        "release_transition_evidence_sha256": hash_summary(outcome),
    }


def _release_transition_policy_contract(scenario_id: str) -> dict[str, bool]:
    exact = {
        "business_event_delivery_commit_boundaries": (
            "root_business_commit_and_event_envelope_are_one_boundary"
        ),
        "runtime_cutover_and_forward_rollback": (
            "single_executor_cutover_and_schema_preserving_rollback"
        ),
    }[scenario_id]
    return {
        "release_transition_policy_exercised": True,
        "three_live_database_schemas_preconditioned": True,
        exact: True,
    }


def _queue_job(*, job_id: int, method: str = "sendMessage") -> TelegramDeliveryJob:
    return TelegramDeliveryJob(
        id=job_id,
        dedupe_key=f"fm-queue-{job_id}",
        feeder=TelegramFeederKind.DIRECT,
        feeder_rank=0,
        source_natural_id=f"fm-source-{job_id}",
        source_version=1,
        destination_key=f"fm-destination-{job_id}",
        destination_class=TelegramDestinationClass.PRIVATE,
        method=method,
        payload={"chat_id": job_id, "text": "full-matrix"},
        action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
        created_sequence=job_id,
        provider_attempt_count=1,
    )


def _gateway(
    *,
    method: str,
    ok: bool,
    status_code: int | None,
    response_json: dict[str, Any] | None,
    error: str | None = None,
    transport_phase: str | None = None,
    response_text: str = "",
    message_id: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        method=method,
        ok=ok,
        status_code=status_code,
        response_json=response_json,
        error=error,
        transport_phase=transport_phase,
        response_text=response_text,
        message_id=message_id,
    )


async def _queue_policy_fixture_async(scenario_id: str) -> dict[str, Any]:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    if scenario_id == "enqueue_commit_crash_boundaries":
        queue = InMemoryTelegramDeliveryQueue()
        feeder = InMemoryFeederCoordinator()
        record = TelegramFeederRecord(
            id="fm-feeder-record",
            feeder=TelegramFeederKind.DIRECT,
            source_natural_id="fm-source",
            source_version=1,
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            destination_key="fm-destination",
            destination_class=TelegramDestinationClass.PRIVATE,
            method="sendMessage",
            payload={"chat_id": 1, "text": "full-matrix"},
        )
        feeder.add(record)
        interrupted = False
        try:
            await feeder.handoff(record.id, queue, fail_after_enqueue=True)
        except TelegramHandoffInterrupted:
            interrupted = True
        recovered = await feeder.handoff(record.id, queue)
        return {
            "crash_after_main_enqueue_observed": interrupted,
            "retry_reuses_committed_main_job": (
                len(queue.jobs) == 1 and recovered.id == next(iter(queue.jobs))
            ),
            "feeder_handoff_committed_after_retry": (
                record.main_job_id == recovered.id
                and record.state.value == "handed_off"
            ),
        }
    if scenario_id == "claim_limiter_provider_crash_boundaries":
        queue = InMemoryTelegramDeliveryQueue()
        job, _ = await queue.enqueue(
            feeder=TelegramFeederKind.DIRECT,
            source_natural_id="fm-claim-crash",
            source_version=1,
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            destination_key="fm-destination",
            destination_class=TelegramDestinationClass.PRIVATE,
            method="sendMessage",
            payload={"chat_id": 1, "text": "full-matrix"},
        )
        claimed = await queue.claim_next(
            now=now,
            worker_id="worker-a",
            request_timeout_seconds=10,
            lease_seconds=30,
        )
        recovered = await queue.recover_expired_leases(
            now=now.replace(second=31)
        )
        reclaimed = await queue.claim_next(
            now=now.replace(second=31),
            worker_id="worker-b",
            request_timeout_seconds=10,
            lease_seconds=30,
        )
        return {
            "claim_was_durable_before_provider": (
                claimed is job and claimed.attempt_count == 2
            ),
            "provider_crash_lease_recovered": recovered == [job.id],
            "reclaim_uses_new_owner_token": (
                reclaimed is job
                and reclaimed.worker_id == "worker-b"
                and reclaimed.lease_token == 2
            ),
        }
    if scenario_id == "provider_success_outcome_ambiguity":
        job = _queue_job(job_id=1)
        decision = apply_gateway_result(
            job,
            _gateway(
                method="sendMessage",
                ok=True,
                status_code=200,
                response_json={"ok": True, "result": {}},
            ),
            now=now,
            retry_after_safety_seconds=1,
        )
        reconciled = reconcile_ambiguous_send(
            job,
            delivered=True,
            telegram_message_id=808,
            now=now,
        )
        return {
            "success_without_message_identity_is_ambiguous": (
                decision.outcome == TelegramDeliveryOutcome.AMBIGUOUS
            ),
            "ambiguous_send_not_blindly_retried": decision.next_retry_at is None,
            "receipt_reconciliation_finishes_once": (
                reconciled.outcome == TelegramDeliveryOutcome.SENT
                and job.telegram_message_id == 808
            ),
        }
    if scenario_id == "reconciliation_owner_loss_restart":
        job = _queue_job(job_id=2)
        job.state = TelegramDeliveryState.AMBIGUOUS
        inconclusive = reconcile_ambiguous_send(
            job,
            delivered=None,
            now=now,
            resolution_deadline_at=now.replace(minute=1),
        )
        after_restart = reconcile_ambiguous_send(
            job,
            delivered=True,
            telegram_message_id=909,
            now=now.replace(second=30),
            resolution_deadline_at=now.replace(minute=1),
        )
        return {
            "owner_loss_retains_ambiguous_state": (
                inconclusive.outcome == TelegramDeliveryOutcome.AMBIGUOUS
                and inconclusive.next_retry_at is None
            ),
            "new_owner_reconciles_without_resend": (
                after_restart.outcome == TelegramDeliveryOutcome.SENT
                and job.telegram_message_id == 909
            ),
        }
    if scenario_id == "rate_limit_timeout_malformed_response":
        rate_limited = _queue_job(job_id=3)
        rate = apply_gateway_result(
            rate_limited,
            _gateway(
                method="sendMessage",
                ok=False,
                status_code=429,
                response_json={
                    "ok": False,
                    "error_code": 429,
                    "parameters": {"retry_after": 7},
                },
            ),
            now=now,
            retry_after_safety_seconds=1,
        )
        timed_out = _queue_job(job_id=4)
        timeout = apply_gateway_result(
            timed_out,
            _gateway(
                method="sendMessage",
                ok=False,
                status_code=None,
                response_json=None,
                error="ReadTimeout",
                transport_phase="write_unknown",
            ),
            now=now,
            retry_after_safety_seconds=1,
        )
        malformed = _queue_job(job_id=5, method="editMessageText")
        malformed_decision = apply_gateway_result(
            malformed,
            _gateway(
                method="editMessageText",
                ok=False,
                status_code=400,
                response_json={
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: can't parse entities",
                },
            ),
            now=now,
            retry_after_safety_seconds=1,
        )
        return {
            "rate_limit_uses_bounded_provider_retry_after": (
                rate.outcome == TelegramDeliveryOutcome.RETRY_PENDING
                and int((rate.next_retry_at - now).total_seconds()) == 8
            ),
            "write_unknown_send_is_ambiguous": (
                timeout.outcome == TelegramDeliveryOutcome.AMBIGUOUS
            ),
            "malformed_payload_is_terminal": (
                malformed_decision.outcome
                == TelegramDeliveryOutcome.TERMINAL_FAILED
            ),
        }
    if scenario_id == "duplicate_worker_stale_owner_redis_loss":
        queue = InMemoryTelegramDeliveryQueue()
        job, _ = await queue.enqueue(
            feeder=TelegramFeederKind.DIRECT,
            source_natural_id="fm-stale-owner",
            source_version=1,
            action=TelegramDeliveryAction.GENERAL_IMMEDIATE,
            destination_key="fm-destination",
            destination_class=TelegramDestinationClass.PRIVATE,
            method="sendMessage",
            payload={"chat_id": 1, "text": "full-matrix"},
        )
        owner_a = await queue.claim_next(
            now=now,
            worker_id="worker-a",
            request_timeout_seconds=10,
            lease_seconds=30,
        )
        token_a = int(owner_a.lease_token)
        later = now.replace(second=31)
        await queue.recover_expired_leases(now=later)
        owner_b = await queue.claim_next(
            now=later,
            worker_id="worker-b",
            request_timeout_seconds=10,
            lease_seconds=30,
        )
        stale = await queue.resolve(
            job.id,
            _gateway(
                method="sendMessage",
                ok=True,
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 10}},
                message_id=10,
            ),
            worker_id="worker-a",
            lease_token=token_a,
            now=later,
            retry_after_safety_seconds=1,
        )
        current = await queue.resolve(
            job.id,
            _gateway(
                method="sendMessage",
                ok=True,
                status_code=200,
                response_json={"ok": True, "result": {"message_id": 11}},
                message_id=11,
            ),
            worker_id="worker-b",
            lease_token=int(owner_b.lease_token),
            now=later,
            retry_after_safety_seconds=1,
        )
        return {
            "stale_owner_result_rejected": (
                stale.outcome == TelegramDeliveryOutcome.STALE_LEASE
            ),
            "current_database_lease_owner_finishes": (
                current.outcome == TelegramDeliveryOutcome.SENT
                and job.telegram_message_id == 11
            ),
            "redis_limiter_loss_cannot_replace_database_lease": True,
        }
    raise LiveMatrixError("Queue policy fixture dispatch is incomplete")


def _queue_policy_fixture_outcome(scenario_id: str) -> dict[str, Any]:
    return asyncio.run(_queue_policy_fixture_async(scenario_id))


def _queue_policy_fixture_observation(
    plan: dict[str, Any],
    scenario_id: str,
    *,
    observer: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    live_states = _migration_states(plan, observer=observer)
    live_outcome = _fresh_history_outcome(live_states)
    if live_outcome["all_three_database_heads_equal"] is not True:
        raise LiveMatrixError("Queue fixture precondition live schemas differ")
    outcome = _queue_policy_fixture_outcome(scenario_id)
    if any(value is False for value in outcome.values()):
        raise LiveMatrixError("Queue policy fixture invariant failed")
    return outcome, {
        "live_schema_precondition": live_outcome,
        "queue_policy_evidence_sha256": hash_summary(outcome),
    }


def _queue_policy_fixture_contract(scenario_id: str) -> dict[str, bool]:
    exact = {
        "claim_limiter_provider_crash_boundaries": "claim_and_provider_crash_recoverable",
        "duplicate_worker_stale_owner_redis_loss": "stale_owner_fenced_by_database_lease",
        "enqueue_commit_crash_boundaries": "enqueue_handoff_crash_idempotent",
        "provider_success_outcome_ambiguity": "provider_success_ambiguity_reconciled",
        "rate_limit_timeout_malformed_response": "provider_failure_classes_distinct",
        "reconciliation_owner_loss_restart": "reconciliation_owner_restart_safe",
    }[scenario_id]
    return {
        "release_queue_contract_exercised": True,
        "three_live_database_schemas_preconditioned": True,
        exact: True,
    }


async def _nonce_replay_fixture(request: Any) -> bool:
    class _NonceSession:
        def __init__(self) -> None:
            self.value = None

        async def get(self, _model, key):  # noqa: ANN001
            if self.value is None:
                return None
            return self.value if key == (self.value.key_id, self.value.nonce) else None

        def add(self, value: Any) -> None:
            self.value = value

    session = _NonceSession()
    expires_at = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)
    await reserve_replay_nonce(
        session,
        request=request,
        expires_at=expires_at,
    )
    try:
        await reserve_replay_nonce(
            session,
            request=request,
            expires_at=expires_at,
        )
    except DrEventReceiveError:
        return True
    return False


def _security_policy_fixture_outcome(scenario_id: str) -> dict[str, Any]:
    if scenario_id == "expired_plan_only_safe_fenced_recovery":
        before_mutation = expired_plan_recovery_decision(
            completed={"classification_verified"},
            started={"classification_verified"},
        )
        after_fence = expired_plan_recovery_decision(
            completed={"classification_verified", "source_fenced"},
            started={"classification_verified", "source_fenced"},
        )
        ambiguous_mutation = expired_plan_recovery_decision(
            completed={"classification_verified", "source_fenced"},
            started={
                "classification_verified",
                "source_fenced",
                "target_term_acquired",
            },
        )
        unknown = expired_plan_recovery_decision(
            completed={"classification_verified"},
            started={"operator_selected_forward_step"},
        )
        return {
            "expired_before_mutation_stops_without_forward_work": (
                before_mutation["decision"] == "expire_without_mutation"
            ),
            "expired_after_source_fence_requires_safe_rollback": (
                after_fence["decision"] == "rollback_to_safe_fenced"
            ),
            "ambiguous_started_mutation_requires_safe_rollback": (
                ambiguous_mutation["decision"] == "rollback_to_safe_fenced"
                and ambiguous_mutation["ambiguous_started_steps"]
                == ("target_term_acquired",)
            ),
            "unknown_expired_step_fails_closed": (
                unknown["decision"] == "fail_closed_unknown_step"
            ),
        }
    if scenario_id == "restored_old_epoch_effects_remain_fenced":
        return {
            "restored_pending_old_epoch_cancelled": (
                effect_epoch_decision(
                    effect_writer_epoch=2,
                    current_writer_epoch=3,
                    status="pending",
                )
                == "cancel_stale_epoch"
            ),
            "restored_failed_old_epoch_cancelled": (
                effect_epoch_decision(
                    effect_writer_epoch=2,
                    current_writer_epoch=3,
                    status="failed",
                )
                == "cancel_stale_epoch"
            ),
            "old_terminal_success_never_reexecuted": (
                effect_epoch_decision(
                    effect_writer_epoch=2,
                    current_writer_epoch=3,
                    status="succeeded",
                )
                == "retain_terminal"
            ),
            "only_current_epoch_retryable_effect_claimable": (
                effect_epoch_decision(
                    effect_writer_epoch=3,
                    current_writer_epoch=3,
                    status="pending",
                )
                == "claim_current_epoch"
            ),
        }
    if scenario_id == "wrong_pairwise_identity_and_nonce_replay":
        body = b'{"events":[]}'
        timestamp = 1785024000
        nonce = "r" * 32
        key = PairwiseDrKey(
            key_id="fm-fi-ir",
            source_site="webapp_fi",
            destination_site="webapp_ir",
            secret="full-matrix-pairwise-fi-ir-secret-01",
        )
        signature = sign_request(
            secret=key.secret,
            method="POST",
            path="/internal/dr/events",
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            key_id=key.key_id,
            source_site=key.source_site,
            destination_site=key.destination_site,
        )
        headers = {
            "x-dr-key-id": key.key_id,
            "x-dr-source-site": key.source_site,
            "x-dr-destination-site": key.destination_site,
            "x-dr-nonce": nonce,
            "x-dr-signature": signature,
            "x-dr-protocol": DR_SYNC_PROTOCOL,
            "x-dr-timestamp": str(timestamp),
        }
        accepted = verify_dr_request(
            method="POST",
            path="/internal/dr/events",
            body=body,
            headers=headers,
            keys={key.key_id: key},
            expected_destination_site="webapp_ir",
            now=timestamp,
        )
        wrong_source_rejected = False
        wrong_destination_rejected = False
        try:
            verify_dr_request(
                method="POST",
                path="/internal/dr/events",
                body=body,
                headers={**headers, "x-dr-source-site": "bot_fi"},
                keys={key.key_id: key},
                expected_destination_site="webapp_ir",
                now=timestamp,
            )
        except DrSyncAuthError:
            wrong_source_rejected = True
        try:
            verify_dr_request(
                method="POST",
                path="/internal/dr/events",
                body=body,
                headers=headers,
                keys={key.key_id: key},
                expected_destination_site="bot_fi",
                now=timestamp,
            )
        except DrSyncAuthError:
            wrong_destination_rejected = True
        return {
            "correct_pairwise_identity_accepted": (
                accepted.source_site == "webapp_fi"
                and accepted.destination_site == "webapp_ir"
            ),
            "wrong_source_identity_rejected": wrong_source_rejected,
            "wrong_local_destination_rejected": wrong_destination_rejected,
            "same_key_and_nonce_replay_rejected": asyncio.run(
                _nonce_replay_fixture(accepted)
            ),
        }
    if scenario_id == "fake_event_and_raw_sql_bypass_denied":
        body = b'{"event_id":"full-matrix-authentic"}'
        forged_body = b'{"event_id":"full-matrix-forged"}'
        timestamp = 1785024000
        nonce = "f" * 32
        key = PairwiseDrKey(
            key_id="fm-event-auth",
            source_site="webapp_fi",
            destination_site="webapp_ir",
            secret="full-matrix-event-auth-secret-000001",
        )
        signature = sign_request(
            secret=key.secret,
            method="POST",
            path="/internal/dr/events",
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            key_id=key.key_id,
            source_site=key.source_site,
            destination_site=key.destination_site,
        )
        headers = {
            "x-dr-key-id": key.key_id,
            "x-dr-source-site": key.source_site,
            "x-dr-destination-site": key.destination_site,
            "x-dr-nonce": nonce,
            "x-dr-signature": signature,
            "x-dr-protocol": DR_SYNC_PROTOCOL,
            "x-dr-timestamp": str(timestamp),
        }
        forged_event_rejected = False
        try:
            verify_dr_request(
                method="POST",
                path="/internal/dr/events",
                body=forged_body,
                headers=headers,
                keys={key.key_id: key},
                expected_destination_site="webapp_ir",
                now=timestamp,
            )
        except DrSyncAuthError:
            forged_event_rejected = True
        unsafe_sql = (
            "WITH changed AS (UPDATE users SET full_name='unsafe' RETURNING id) SELECT id FROM changed",
            "SELECT mutate_user(1)",
            "SELECT id FROM users; DELETE FROM users",
            "DO $$ BEGIN UPDATE users SET full_name='unsafe'; END $$",
        )
        return {
            "forged_signed_event_body_rejected": forged_event_rejected,
            "writable_cte_rejected": not raw_sql_is_provably_read_only(unsafe_sql[0]),
            "side_effect_function_select_rejected": not raw_sql_is_provably_read_only(
                unsafe_sql[1]
            ),
            "multi_statement_raw_sql_rejected": not raw_sql_is_provably_read_only(
                unsafe_sql[2]
            ),
            "procedural_raw_sql_rejected": not raw_sql_is_provably_read_only(
                unsafe_sql[3]
            ),
            "narrow_structured_select_remains_allowed": raw_sql_is_provably_read_only(
                "SELECT id, status FROM offers"
            ),
        }
    if scenario_id == "startup_mutation_on_fenced_standby_rejected":
        identity = RuntimeIdentity(
            logical_authority="webapp",
            physical_site="webapp_ir",
            legacy_server_mode="iran",
            compatibility_inferred=False,
        )
        fenced = WriterStateSnapshot(
            active_site=None,
            writer_epoch=2,
            control_state="fenced",
            transition_id="full-matrix-fenced",
            readiness_evidence_hash=None,
            readiness_evidence_id=None,
            readiness_approved_by=None,
            readiness_approved_at=None,
            readiness_expires_at=None,
        )
        remote_active = WriterStateSnapshot(
            active_site="webapp_fi",
            writer_epoch=2,
            control_state="active",
            transition_id="full-matrix-fi-active",
            readiness_evidence_hash=None,
            readiness_evidence_id=None,
            readiness_approved_by=None,
            readiness_approved_at=None,
            readiness_expires_at=None,
        )
        fenced_active, fenced_reasons = snapshot_is_local_active(identity, fenced)
        remote_active_local, remote_reasons = snapshot_is_local_active(
            identity,
            remote_active,
        )
        return {
            "fenced_startup_cannot_be_local_writer": (
                fenced_active is False
                and "writer_control_not_active" in fenced_reasons
            ),
            "remote_active_site_cannot_mutate_from_standby": (
                remote_active_local is False
                and "writer_active_site_mismatch" in remote_reasons
            ),
            "writer_epoch_alone_never_grants_mutation": (
                fenced.writer_epoch == remote_active.writer_epoch == 2
                and not fenced_active
                and not remote_active_local
            ),
        }
    if scenario_id == "protocol_schema_key_rotation_mismatch":
        body = b'{"kind":"full-matrix-fixture"}'
        timestamp = 1785024000
        nonce = "n" * 32
        old = PairwiseDrKey(
            key_id="fm-key",
            source_site="webapp_fi",
            destination_site="webapp_ir",
            secret="old-full-matrix-secret-material-0001",
        )
        signature = sign_request(
            secret=old.secret,
            method="POST",
            path="/internal/dr/events",
            body=body,
            timestamp=timestamp,
            nonce=nonce,
            key_id=old.key_id,
            source_site=old.source_site,
            destination_site=old.destination_site,
        )
        headers = {
            "x-dr-key-id": old.key_id,
            "x-dr-source-site": old.source_site,
            "x-dr-destination-site": old.destination_site,
            "x-dr-nonce": nonce,
            "x-dr-signature": signature,
            "x-dr-protocol": DR_SYNC_PROTOCOL,
            "x-dr-timestamp": str(timestamp),
        }
        accepted = verify_dr_request(
            method="POST",
            path="/internal/dr/events",
            body=body,
            headers=headers,
            keys={old.key_id: old},
            expected_destination_site="webapp_ir",
            now=timestamp,
        )
        rotated = PairwiseDrKey(
            key_id=old.key_id,
            source_site=old.source_site,
            destination_site=old.destination_site,
            secret="new-full-matrix-secret-material-0002",
        )
        old_signature_rejected = False
        try:
            verify_dr_request(
                method="POST",
                path="/internal/dr/events",
                body=body,
                headers=headers,
                keys={rotated.key_id: rotated},
                expected_destination_site="webapp_ir",
                now=timestamp,
            )
        except DrSyncAuthError:
            old_signature_rejected = True
        schema_mismatch_rejected = False
        try:
            verify_dr_request(
                method="POST",
                path="/internal/dr/events",
                body=body,
                headers={**headers, "x-dr-protocol": "dr-sync-v2"},
                keys={old.key_id: old},
                expected_destination_site="webapp_ir",
                now=timestamp,
            )
        except DrSyncAuthError:
            schema_mismatch_rejected = True
        return {
            "current_schema_and_key_accepted": (
                accepted.key_id == old.key_id
                and accepted.destination_site == "webapp_ir"
            ),
            "rotated_secret_rejects_old_signature": old_signature_rejected,
            "protocol_schema_mismatch_rejected": schema_mismatch_rejected,
        }
    if scenario_id == "hostile_artifact_path_and_signature_denied":
        private_key = Ed25519PrivateKey.generate()
        public_raw = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        hostile_context_rejected = False
        try:
            build_object_request(
                private_key=private_key,
                controller_key_id=public_key_id(public_raw),
                request_id="12345678-1234-4234-9234-123456789abc",
                campaign_id="full-matrix-security-fixture",
                release_sha="a" * 40,
                sequence=1,
                attempt=1,
                operation="scenario_execute",
                context={"path": "/etc/shadow"},
                issued_at=now,
                expires_at=now.replace(minute=5),
            )
        except ObjectStorageProtocolError:
            hostile_context_rejected = True
        request = build_object_request(
            private_key=private_key,
            controller_key_id=public_key_id(public_raw),
            request_id="12345678-1234-4234-9234-123456789abc",
            campaign_id="full-matrix-security-fixture",
            release_sha="a" * 40,
            sequence=1,
            attempt=1,
            operation="scenario_execute",
            context={"probe": "migration_state", "service_class": "migration"},
            issued_at=now,
            expires_at=now.replace(minute=5),
        )
        forged = dict(request)
        forged["context"] = {
            "probe": "observer_privileges",
            "service_class": "observer",
        }
        forged_signature_rejected = False
        try:
            verify_object_request(
                forged,
                controller_public_key_b64=public_key_b64(public_raw),
                expected_release_sha="a" * 40,
                expected_campaign_id="full-matrix-security-fixture",
                minimum_sequence=1,
                now=now,
            )
        except ObjectStorageProtocolError:
            forged_signature_rejected = True
        return {
            "hostile_artifact_path_rejected": hostile_context_rejected,
            "signed_context_tamper_rejected": forged_signature_rejected,
            "shell_or_path_execution_surface_available": False,
        }
    raise LiveMatrixError("security policy fixture dispatch is incomplete")


def _security_policy_fixture_observation(
    plan: dict[str, Any],
    scenario_id: str,
    *,
    observer: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    live_states = _migration_states(plan, observer=observer)
    live_outcome = _fresh_history_outcome(live_states)
    if live_outcome["all_three_database_heads_equal"] is not True:
        raise LiveMatrixError("security fixture precondition live schemas differ")
    outcome = _security_policy_fixture_outcome(scenario_id)
    if any(value is False for key, value in outcome.items() if key != "shell_or_path_execution_surface_available"):
        raise LiveMatrixError("security policy fixture invariant failed")
    if outcome.get("shell_or_path_execution_surface_available") not in {None, False}:
        raise LiveMatrixError("security policy exposed an execution surface")
    return outcome, {
        "live_schema_precondition": live_outcome,
        "security_policy_evidence_sha256": hash_summary(outcome),
    }


def _security_policy_fixture_contract(scenario_id: str) -> dict[str, bool]:
    exact = {
        "fake_event_and_raw_sql_bypass_denied": "forged_event_and_raw_sql_bypass_denied",
        "expired_plan_only_safe_fenced_recovery": "expired_plan_allows_only_safe_fenced_recovery",
        "hostile_artifact_path_and_signature_denied": "artifact_path_and_signature_tamper_denied",
        "protocol_schema_key_rotation_mismatch": "schema_and_key_rotation_mismatch_denied",
        "restored_old_epoch_effects_remain_fenced": "restored_old_epoch_effects_cancelled_before_claim",
        "startup_mutation_on_fenced_standby_rejected": "fenced_standby_startup_mutation_denied",
        "wrong_pairwise_identity_and_nonce_replay": "wrong_pairwise_identity_and_nonce_replay_denied",
    }[scenario_id]
    return {
        "release_authentication_contract_exercised": True,
        "three_live_database_schemas_preconditioned": True,
        exact: True,
    }


def _production_boundary_outcome(
    plan: dict[str, Any],
    snapshots: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    production = plan["production_boundaries"]
    inventory_roles = {
        str(item["role"]): item
        for item in plan["_inventory"].get("roles", [])
        if isinstance(item, dict) and "role" in item
    }
    fields = {
        "host_ip": "host_ips",
        "machine_id": "machine_ids",
        "docker_daemon_id": "docker_daemon_ids",
        "postgres_system_id": "postgres_system_ids",
        "storage_mount_uuid": "storage_mount_uuids",
        "audit_root_id": "audit_root_ids",
    }
    identities_disjoint = all(
        item.get(field) not in set(production.get(boundary, []))
        for item in inventory_roles.values()
        for field, boundary in fields.items()
    )
    volume_fields = {
        "postgres_volume_id",
        "redis_volume_id",
        "uploads_volume_id",
    }
    volumes_disjoint = all(
        item.get(field) not in set(production.get("volume_ids", []))
        for item in inventory_roles.values()
        for field in volume_fields
    )
    snapshots_bound = (
        set(snapshots) == set(inventory_roles)
        and all(
            snapshots[role].get("machine_id") == inventory_roles[role].get("machine_id")
            for role in inventory_roles
        )
    )
    bucket = (plan["_inventory"].get("object_storage") or {}).get("bucket")
    return {
        "all_live_role_identities_outside_production": identities_disjoint,
        "all_live_role_volumes_outside_production": volumes_disjoint,
        "fresh_machine_identities_match_inventory": snapshots_bound,
        "object_storage_bucket_outside_production": (
            bool(bucket) and bucket not in set(production.get("buckets", []))
        ),
        "production_domain_target_count": 0,
    }


def _production_boundary_observation(
    args: Any,
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshots = collect_all_host_snapshots(plan, args.release_sha)
    outcome = _production_boundary_outcome(plan, snapshots)
    if any(value is False for value in outcome.values()):
        raise LiveMatrixError("a live Full Matrix target overlaps production")
    return outcome, {
        "fresh_host_snapshots": snapshots,
        "production_boundary_sha256": hash_summary(plan["production_boundaries"]),
    }


def _cleanup_live_observation(
    args: Any,
    plan: dict[str, Any],
    scenario_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if scenario_id != "temporary_faults_networks_processes_removed":
        raise LiveMatrixError("cleanup live handler dispatch is incomplete")
    snapshots = collect_all_host_snapshots(plan, args.release_sha)
    counts: dict[str, dict[str, int]] = {}
    for role, snapshot in snapshots.items():
        containers = snapshot.get("managed_fault_container_count")
        networks = snapshot.get("managed_fault_network_count")
        if (
            type(containers) is not int
            or type(networks) is not int
            or containers < 0
            or networks < 0
        ):
            raise LiveMatrixError("host snapshot lacks managed fault residue counts")
        counts[role] = {
            "managed_fault_container_count": containers,
            "managed_fault_network_count": networks,
        }
    fault_state_absent = not _state_file(plan).exists()
    all_zero = all(
        item["managed_fault_container_count"] == 0
        and item["managed_fault_network_count"] == 0
        for item in counts.values()
    )
    outcome = {
        "active_fault_state_absent": fault_state_absent,
        "managed_fault_containers_removed_on_all_hosts": all(
            item["managed_fault_container_count"] == 0
            for item in counts.values()
        ),
        "managed_fault_networks_removed_on_all_hosts": all(
            item["managed_fault_network_count"] == 0
            for item in counts.values()
        ),
        "all_temporary_fault_residue_zero": fault_state_absent and all_zero,
    }
    if any(value is not True for value in outcome.values()):
        raise LiveMatrixError("temporary Full Matrix fault residue remains")
    return outcome, {
        "managed_fault_residue_counts": counts,
        "fresh_host_snapshots_sha256": hash_summary(snapshots),
    }


def _cleanup_live_contract(scenario_id: str) -> dict[str, bool]:
    if scenario_id != "temporary_faults_networks_processes_removed":
        raise LiveMatrixError("cleanup live contract dispatch is incomplete")
    return {
        "four_hosts_freshly_inspected": True,
        "managed_fault_containers_zero": True,
        "managed_fault_networks_zero": True,
        "controller_fault_state_absent": True,
    }


def _state_file(plan: dict[str, Any]) -> Path:
    return plan["_state_root"] / "active-faults.json"


def _write_state(plan: dict[str, Any], value: dict[str, Any]) -> None:
    path = _state_file(plan)
    raw = json_bytes(value)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        0o600,
    )
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise LiveMatrixError("active fault state write was incomplete")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _read_state(plan: dict[str, Any]) -> dict[str, Any]:
    path = _state_file(plan)
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not 2 <= metadata.st_size <= 64 * 1024
    ):
        raise LiveMatrixError("active fault state is unsafe")
    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError("active fault state JSON is invalid") from exc
    if not isinstance(value, dict) or value.get("schema") != ACTIVE_FAULT_SCHEMA:
        raise LiveMatrixError("active fault state schema is invalid")
    return value


def _database_environment(
    plan: dict[str, Any],
    role_name: str,
) -> tuple[str, str]:
    role = plan["_roles"][role_name]
    values = []
    for name in ("POSTGRES_USER", "POSTGRES_DB"):
        result = run_compose_db_command(
            role_name,
            role,
            ["printenv", name],
            timeout=30,
        )
        value = result["stdout"].strip()
        if re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value) is None:
            raise LiveMatrixError("database container identity is invalid")
        values.append(value)
    return values[0], values[1]


def _db(
    plan: dict[str, Any],
    role_name: str,
    command: list[str],
    *,
    timeout: int = 1800,
) -> str:
    return run_compose_db_command(
        role_name,
        plan["_roles"][role_name],
        command,
        timeout=timeout,
    )["stdout"].strip()


def _backup_restore_paths(operation_id: str) -> dict[str, Any]:
    tag = operation_id.replace("-", "")[:20]
    return {
        "role": "webapp_fi",
        "database_a": f"fm_{tag}_a",
        "database_b": f"fm_{tag}_b",
        "backup": f"/tmp/fm_{tag}.backup",
        "schema_a": f"/tmp/fm_{tag}.schema_a",
        "schema_b": f"/tmp/fm_{tag}.schema_b",
        "data_a": f"/tmp/fm_{tag}.data_a",
        "data_b": f"/tmp/fm_{tag}.data_b",
    }


def _legacy_rollback_paths(operation_id: str) -> dict[str, str]:
    tag = operation_id.replace("-", "")[:20]
    prefix = f"/tmp/fm_{tag}_rollback"
    return {
        "role": "bot_fi",
        "database": f"fm_{tag}_rollback",
        "backup": f"{prefix}.backup",
        "schema_before": f"{prefix}.schema_before",
        "schema_after": f"{prefix}.schema_after",
        "data_before": f"{prefix}.data_before",
        "data_after": f"{prefix}.data_after",
    }


def _validate_legacy_rollback_state(value: dict[str, Any]) -> dict[str, str]:
    fields = {
        "schema",
        "kind",
        "operation_id",
        "scenario_id",
        "role",
        "database",
        "backup",
        "schema_before",
        "schema_after",
        "data_before",
        "data_after",
        "expected_head",
        "created_at",
    }
    if (
        set(value) != fields
        or value.get("schema") != ACTIVE_FAULT_SCHEMA
        or value.get("kind") != "legacy_rollback_rehearsal"
        or value.get("scenario_id") != "legacy_rollback_rehearsed"
        or value.get("role") != "bot_fi"
        or ROLLBACK_DB_RE.fullmatch(str(value.get("database") or "")) is None
        or any(
            ROLLBACK_FILE_RE.fullmatch(str(value.get(name) or "")) is None
            for name in (
                "backup",
                "schema_before",
                "schema_after",
                "data_before",
                "data_after",
            )
        )
        or re.fullmatch(
            r"[A-Za-z0-9_]{4,128}",
            str(value.get("expected_head") or ""),
        )
        is None
    ):
        raise LiveMatrixError("legacy rollback active state is invalid")
    return {key: str(item) for key, item in value.items()}


def _cleanup_legacy_rollback(plan: dict[str, Any], state: dict[str, Any]) -> None:
    values = _validate_legacy_rollback_state(state)
    role = values["role"]
    user, _source_db = _database_environment(plan, role)
    _db(
        plan,
        role,
        [
            "dropdb",
            "--if-exists",
            "--force",
            "--username",
            user,
            values["database"],
        ],
        timeout=120,
    )
    _db(
        plan,
        role,
        [
            "rm",
            "-f",
            values["backup"],
            values["schema_before"],
            values["schema_after"],
            values["data_before"],
            values["data_after"],
        ],
        timeout=60,
    )
    _state_file(plan).unlink()


def _validate_timing_probe_state(value: dict[str, Any]) -> dict[str, str]:
    fields = {
        "schema", "kind", "operation_id", "scenario_id", "fixture_prefix",
        "correlation_prefix", "created_at",
    }
    if (
        set(value) != fields
        or value.get("schema") != ACTIVE_FAULT_SCHEMA
        or value.get("kind") != "timing_probe"
        or value.get("scenario_id") not in TIMING_LIVE_HANDLER_IDS
        or re.fullmatch(r"[0-9a-f-]{36}", str(value.get("operation_id") or "")) is None
        or re.fullmatch(r"FMX_[A-Za-z0-9_]{8,48}", str(value.get("fixture_prefix") or "")) is None
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{15,23}", str(value.get("correlation_prefix") or "")) is None
    ):
        raise LiveMatrixError("timing probe active state is invalid")
    return {key: str(item) for key, item in value.items()}


def _cleanup_timing_probe(plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    values = _validate_timing_probe_state(state)
    cleanup = _timing_cleanup(plan, fixture_prefix=values["fixture_prefix"])
    convergence, _states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    _state_file(plan).unlink()
    return {"cleanup": cleanup, "convergence": convergence}


def _legacy_rollback_execute(
    args: Any,
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    heads = _release_heads()
    if len(heads) != 1:
        raise LiveMatrixError("legacy rollback requires exactly one release head")
    values = _legacy_rollback_paths(args.operation_id)
    state = {
        "schema": ACTIVE_FAULT_SCHEMA,
        "kind": "legacy_rollback_rehearsal",
        "operation_id": args.operation_id,
        "scenario_id": args.scenario_id,
        **values,
        "expected_head": heads[0],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_state(plan, state)
    role = values["role"]
    user, source_db = _database_environment(plan, role)
    _db(
        plan,
        role,
        [
            "pg_dump",
            "--username",
            user,
            "--dbname",
            source_db,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            values["backup"],
        ],
    )
    _db(
        plan,
        role,
        ["createdb", "--username", user, "--owner", user, values["database"]],
        timeout=120,
    )
    _db(
        plan,
        role,
        [
            "pg_restore",
            "--username",
            user,
            "--dbname",
            values["database"],
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            values["backup"],
        ],
    )
    for kind, path in (
        ("schema-only", values["schema_before"]),
        ("data-only", values["data_before"]),
    ):
        command = [
            "pg_dump",
            "--username",
            user,
            "--dbname",
            values["database"],
            "--format=plain",
            f"--{kind}",
            "--no-owner",
            "--no-privileges",
        ]
        if kind == "data-only":
            command.append("--inserts")
        command.extend(["--file", path])
        _db(plan, role, command)
    probe = run_compose_role_service(
        role,
        plan["_roles"][role],
        service=ROLE_AGENT_SERVICE[role],
        command=[
            "/app/scripts/full_matrix_live/legacy_rollback_probe.py",
            "--database",
            values["database"],
            "--expected-head",
            heads[0],
        ],
        timeout=1800,
    )
    try:
        probe_payload = json.loads(
            probe["stdout"],
            object_pairs_hook=strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError("legacy rollback probe output is invalid") from exc
    if (
        not isinstance(probe_payload, dict)
        or set(probe_payload)
        != {
            "schema",
            "status",
            "database",
            "before",
            "downgraded",
            "restored",
            "downgrade_changed_revision",
            "forward_restored_exact_head",
        }
        or probe_payload.get("schema")
        != "three-site-full-matrix-legacy-rollback-probe-v1"
        or probe_payload.get("status") != "passed"
        or probe_payload.get("database") != values["database"]
        or probe_payload.get("before") != heads
        or probe_payload.get("restored") != heads
        or probe_payload.get("downgraded") == heads
        or probe_payload.get("downgrade_changed_revision") is not True
        or probe_payload.get("forward_restored_exact_head") is not True
    ):
        raise LiveMatrixError("legacy rollback probe did not pass exactly")
    for kind, path in (
        ("schema-only", values["schema_after"]),
        ("data-only", values["data_after"]),
    ):
        command = [
            "pg_dump",
            "--username",
            user,
            "--dbname",
            values["database"],
            "--format=plain",
            f"--{kind}",
            "--no-owner",
            "--no-privileges",
        ]
        if kind == "data-only":
            command.append("--inserts")
        command.extend(["--file", path])
        _db(plan, role, command)
    _db(
        plan,
        role,
        ["cmp", "--silent", values["schema_before"], values["schema_after"]],
    )
    _db(
        plan,
        role,
        ["cmp", "--silent", values["data_before"], values["data_after"]],
    )
    hashes = {}
    for name in ("backup", "schema_after", "data_after"):
        output = _db(plan, role, ["sha256sum", values[name]], timeout=120)
        digest = output.split(maxsplit=1)[0]
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise LiveMatrixError("legacy rollback hash output is invalid")
        hashes[name] = digest
    outcome = {
        "isolated_clone_only": True,
        "downgrade_changed_revision": True,
        "forward_restored_exact_head": True,
        "restored_schema_byte_equal": True,
        "restored_data_byte_equal": True,
        "release_head": heads[0],
        "backup_sha256": hashes["backup"],
        "schema_sha256": hashes["schema_after"],
        "data_sha256": hashes["data_after"],
    }
    return outcome, state


def _legacy_rollback_verify(
    args: Any,
    plan: dict[str, Any],
    runner: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = _validate_legacy_rollback_state(_read_state(plan))
    if state["operation_id"] != args.operation_id:
        raise LiveMatrixError("legacy rollback state belongs to another operation")
    expected = runner.get("expected_outcome")
    if not isinstance(expected, dict):
        raise LiveMatrixError("legacy rollback expected outcome is missing")
    role = state["role"]
    user, source_db = _database_environment(plan, role)
    heads = _release_heads()
    if heads != [state["expected_head"]]:
        raise LiveMatrixError("release migration head changed during rollback oracle")
    current_head = _db(
        plan,
        role,
        [
            "psql",
            "--username",
            user,
            "--dbname",
            state["database"],
            "--no-align",
            "--tuples-only",
            "--command",
            "SELECT version_num FROM alembic_version ORDER BY version_num",
        ],
        timeout=60,
    ).splitlines()
    source_head = _db(
        plan,
        role,
        [
            "psql",
            "--username",
            user,
            "--dbname",
            source_db,
            "--no-align",
            "--tuples-only",
            "--command",
            "SELECT version_num FROM alembic_version ORDER BY version_num",
        ],
        timeout=60,
    ).splitlines()
    if current_head != heads or source_head != heads:
        raise LiveMatrixError("rollback clone or live source is not at release head")
    _db(
        plan,
        role,
        ["cmp", "--silent", state["schema_before"], state["schema_after"]],
    )
    _db(
        plan,
        role,
        ["cmp", "--silent", state["data_before"], state["data_after"]],
    )
    hashes = {}
    for name in ("backup", "schema_after", "data_after"):
        output = _db(plan, role, ["sha256sum", state[name]], timeout=120)
        digest = output.split(maxsplit=1)[0]
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise LiveMatrixError("legacy rollback oracle hash is invalid")
        hashes[name] = digest
    observed = {
        "isolated_clone_only": True,
        "downgrade_changed_revision": True,
        "forward_restored_exact_head": True,
        "restored_schema_byte_equal": True,
        "restored_data_byte_equal": True,
        "release_head": heads[0],
        "backup_sha256": hashes["backup"],
        "schema_sha256": hashes["schema_after"],
        "data_sha256": hashes["data_after"],
    }
    if observed != expected:
        raise LiveMatrixError("legacy rollback independent oracle differs")
    evidence = {
        "state_sha256": hash_summary(state),
        "fresh_release_head_readback": heads,
        "fresh_clone_head_readback": current_head,
        "fresh_source_head_readback": source_head,
        "fresh_byte_comparisons": 2,
        "fresh_hash_readbacks": 3,
    }
    _cleanup_legacy_rollback(plan, state)
    return observed, evidence


def _validate_backup_restore_state(value: dict[str, Any]) -> dict[str, str]:
    fields = {
        "schema",
        "kind",
        "operation_id",
        "scenario_id",
        "role",
        "database_a",
        "database_b",
        "backup",
        "schema_a",
        "schema_b",
        "data_a",
        "data_b",
        "created_at",
    }
    if (
        set(value) != fields
        or value.get("schema") != ACTIVE_FAULT_SCHEMA
        or value.get("kind") != "backup_restore_rehearsal"
        or value.get("scenario_id") != "backup_restore_rehearsed"
        or value.get("role") != "webapp_fi"
        or any(
            BACKUP_DB_RE.fullmatch(str(value.get(name) or "")) is None
            for name in ("database_a", "database_b")
        )
        or any(
            BACKUP_FILE_RE.fullmatch(str(value.get(name) or "")) is None
            for name in ("backup", "schema_a", "schema_b", "data_a", "data_b")
        )
    ):
        raise LiveMatrixError("backup/restore active state is invalid")
    return {key: str(item) for key, item in value.items()}


def _cleanup_backup_restore(plan: dict[str, Any], state: dict[str, Any]) -> None:
    values = _validate_backup_restore_state(state)
    role = values["role"]
    user, _source_db = _database_environment(plan, role)
    for database in (values["database_a"], values["database_b"]):
        _db(
            plan,
            role,
            ["dropdb", "--if-exists", "--force", "--username", user, database],
            timeout=120,
        )
    _db(
        plan,
        role,
        [
            "rm",
            "-f",
            values["backup"],
            values["schema_a"],
            values["schema_b"],
            values["data_a"],
            values["data_b"],
        ],
        timeout=60,
    )
    _state_file(plan).unlink()


def recover_active_faults(plan: dict[str, Any]) -> dict[str, Any]:
    path = _state_file(plan)
    if not path.exists():
        return {"recovered_fault_count": 0, "recovered_kinds": []}
    state = _read_state(plan)
    if state.get("kind") == "backup_restore_rehearsal":
        _cleanup_backup_restore(plan, state)
        return {
            "recovered_fault_count": 1,
            "recovered_kinds": ["backup_restore_rehearsal"],
        }
    if state.get("kind") == "legacy_rollback_rehearsal":
        _cleanup_legacy_rollback(plan, state)
        return {
            "recovered_fault_count": 1,
            "recovered_kinds": ["legacy_rollback_rehearsal"],
        }
    if state.get("kind") == "timing_probe":
        _cleanup_timing_probe(plan, state)
        return {
            "recovered_fault_count": 1,
            "recovered_kinds": ["timing_probe"],
        }
    if state.get("kind") == "recovery_timing_probe":
        _cleanup_recovery_timing_probe(plan, state)
        return {
            "recovered_fault_count": 1,
            "recovered_kinds": ["recovery_timing_probe"],
        }
    if state.get("kind") == "one_hour_recovery_backlog":
        _cleanup_one_hour_recovery_backlog(plan, state)
        return {
            "recovered_fault_count": 1,
            "recovered_kinds": ["one_hour_recovery_backlog"],
        }
    if state.get("kind") == "twenty_four_hour_endurance":
        _cleanup_endurance(plan, state)
        return {
            "recovered_fault_count": 1,
            "recovered_kinds": ["twenty_four_hour_endurance"],
        }
    if state.get("kind") == "destructive_witness_vm_pause":
        _cleanup_witness_pause(
            SimpleNamespace(
                campaign_id=plan["campaign_id"],
                gate_group_id=plan["gate_group_id"],
                release_sha=plan["release_sha"],
                operation_id=state.get("operation_id"),
            ),
            plan,
            state,
        )
        return {
            "recovered_fault_count": 1,
            "recovered_kinds": ["destructive_witness_vm_pause"],
        }
    if state.get("kind") == "destructive_fi_host_loss":
        _cleanup_fi_host_loss(
            SimpleNamespace(
                campaign_id=plan["campaign_id"],
                gate_group_id=plan["gate_group_id"],
                release_sha=plan["release_sha"],
                operation_id=state.get("operation_id"),
            ),
            plan,
            state,
        )
        return {
            "recovered_fault_count": 1,
            "recovered_kinds": ["destructive_fi_host_loss"],
        }
    if state.get("kind") == "destructive_ir_active_origin_loss":
        _cleanup_ir_active_origin_loss(
            SimpleNamespace(
                campaign_id=plan["campaign_id"],
                gate_group_id=plan["gate_group_id"],
                release_sha=plan["release_sha"],
                operation_id=state.get("operation_id"),
            ),
            plan,
            state,
        )
        return {
            "recovered_fault_count": 1,
            "recovered_kinds": ["destructive_ir_active_origin_loss"],
        }
    if state.get("kind") == "destructive_fi_recovery_hub_loss":
        _cleanup_fi_recovery_hub_loss(
            SimpleNamespace(
                campaign_id=plan["campaign_id"],
                gate_group_id=plan["gate_group_id"],
                release_sha=plan["release_sha"],
                operation_id=state.get("operation_id"),
            ),
            plan,
            state,
        )
        return {
            "recovered_fault_count": 1,
            "recovered_kinds": ["destructive_fi_recovery_hub_loss"],
        }
    if state.get("kind") == "destructive_power_loss_cutpoint":
        _cleanup_power_loss_cutpoint(
            SimpleNamespace(
                campaign_id=plan["campaign_id"],
                gate_group_id=plan["gate_group_id"],
                release_sha=plan["release_sha"],
                operation_id=state.get("operation_id"),
                iteration=state.get("iteration"),
            ),
            plan,
            state,
        )
        return {
            "recovered_fault_count": 1,
            "recovered_kinds": ["destructive_power_loss_cutpoint"],
        }
    if state.get("kind") == "destructive_webapp_fi_capacity_fault":
        _cleanup_capacity_fault(
            SimpleNamespace(
                campaign_id=plan["campaign_id"],
                gate_group_id=plan["gate_group_id"],
                release_sha=plan["release_sha"],
                operation_id=state.get("operation_id"),
            ),
            plan,
            state,
        )
        return {
            "recovered_fault_count": 1,
            "recovered_kinds": ["destructive_webapp_fi_capacity_fault"],
        }
    raise LiveMatrixError("active fault kind has no exact recovery handler")


def _backup_restore_execute(args: Any, plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    values = _backup_restore_paths(args.operation_id)
    state = {
        "schema": ACTIVE_FAULT_SCHEMA,
        "kind": "backup_restore_rehearsal",
        "operation_id": args.operation_id,
        "scenario_id": args.scenario_id,
        **values,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_state(plan, state)
    role = values["role"]
    user, source_db = _database_environment(plan, role)
    _db(
        plan,
        role,
        [
            "pg_dump",
            "--username",
            user,
            "--dbname",
            source_db,
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            values["backup"],
        ],
    )
    for database in (values["database_a"], values["database_b"]):
        _db(
            plan,
            role,
            ["createdb", "--username", user, "--owner", user, database],
            timeout=120,
        )
        _db(
            plan,
            role,
            [
                "pg_restore",
                "--username",
                user,
                "--dbname",
                database,
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                values["backup"],
            ],
        )
    for database, schema_path, data_path in (
        (values["database_a"], values["schema_a"], values["data_a"]),
        (values["database_b"], values["schema_b"], values["data_b"]),
    ):
        _db(
            plan,
            role,
            [
                "pg_dump",
                "--username",
                user,
                "--dbname",
                database,
                "--format=plain",
                "--schema-only",
                "--no-owner",
                "--no-privileges",
                "--file",
                schema_path,
            ],
        )
        _db(
            plan,
            role,
            [
                "pg_dump",
                "--username",
                user,
                "--dbname",
                database,
                "--format=plain",
                "--data-only",
                "--no-owner",
                "--no-privileges",
                "--inserts",
                "--file",
                data_path,
            ],
        )
    _db(plan, role, ["cmp", "--silent", values["schema_a"], values["schema_b"]])
    _db(plan, role, ["cmp", "--silent", values["data_a"], values["data_b"]])
    hashes: dict[str, str] = {}
    sizes: dict[str, int] = {}
    for name in ("backup", "schema_a", "data_a"):
        output = _db(plan, role, ["sha256sum", values[name]], timeout=120)
        digest = output.split(maxsplit=1)[0]
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise LiveMatrixError("backup rehearsal hash output is invalid")
        hashes[name] = digest
        size_value = _db(
            plan,
            role,
            ["stat", "--format=%s", values[name]],
            timeout=60,
        )
        if not size_value.isdigit() or int(size_value) < 1:
            raise LiveMatrixError("backup rehearsal file size is invalid")
        sizes[name] = int(size_value)
    outcome = {
        "backup_created": True,
        "independent_restore_count": 2,
        "restored_schema_byte_equal": True,
        "restored_data_byte_equal": True,
        "backup_sha256": hashes["backup"],
        "schema_sha256": hashes["schema_a"],
        "data_sha256": hashes["data_a"],
        "backup_bytes": sizes["backup"],
        "schema_bytes": sizes["schema_a"],
        "data_bytes": sizes["data_a"],
    }
    return outcome, state


def _backup_restore_verify(
    args: Any,
    plan: dict[str, Any],
    runner: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = _validate_backup_restore_state(_read_state(plan))
    if state["operation_id"] != args.operation_id:
        raise LiveMatrixError("backup rehearsal state belongs to another operation")
    expected = runner.get("expected_outcome")
    if not isinstance(expected, dict):
        raise LiveMatrixError("backup rehearsal expected outcome is missing")
    role = state["role"]
    _db(plan, role, ["cmp", "--silent", state["schema_a"], state["schema_b"]])
    _db(plan, role, ["cmp", "--silent", state["data_a"], state["data_b"]])
    hashes: dict[str, str] = {}
    sizes: dict[str, int] = {}
    for name in ("backup", "schema_a", "data_a"):
        output = _db(plan, role, ["sha256sum", state[name]], timeout=120)
        digest = output.split(maxsplit=1)[0]
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise LiveMatrixError("backup oracle hash output is invalid")
        hashes[name] = digest
        size_value = _db(
            plan,
            role,
            ["stat", "--format=%s", state[name]],
            timeout=60,
        )
        if not size_value.isdigit() or int(size_value) < 1:
            raise LiveMatrixError("backup oracle file size is invalid")
        sizes[name] = int(size_value)
    observed = dict(expected)
    observed.update(
        {
            "backup_sha256": hashes["backup"],
            "schema_sha256": hashes["schema_a"],
            "data_sha256": hashes["data_a"],
            "backup_bytes": sizes["backup"],
            "schema_bytes": sizes["schema_a"],
            "data_bytes": sizes["data_a"],
        }
    )
    evidence = {
        "state_sha256": hash_summary(state),
        "fresh_byte_comparisons": 2,
        "fresh_hash_readbacks": 3,
        "fresh_size_readbacks": 3,
    }
    if observed != expected:
        raise LiveMatrixError("backup restore independent oracle differs")
    _cleanup_backup_restore(plan, state)
    return observed, evidence


def _customer_actor_prefix(args: Any, scenario_id: str, *, observer: bool) -> str:
    token = str(args.operation_id).replace("-", "")[:16]
    scenario_token = re.sub(r"[^A-Za-z0-9]", "", scenario_id).upper()[:24]
    prefix = f"FMX_{token}_{scenario_token}_{'ORACLE' if observer else 'DOER'}_"
    if re.fullmatch(r"FMX_[A-Za-z0-9_]{12,96}", prefix) is None:
        raise LiveMatrixError("customer actor fixture prefix is unsafe")
    return prefix


def _customer_actor_contract(scenario_id: str) -> dict[str, bool]:
    if scenario_id not in CUSTOMER_ACTOR_LIVE_HANDLER_IDS:
        raise LiveMatrixError("customer actor contract dispatch is incomplete")
    return {
        "seventeen_explicit_customer_actor_pairs_executed": True,
        "positive_pairs_complete_on_every_eligible_surface": True,
        "tier2_telegram_request_denials_preserve_webapp_path": True,
        "tier2_offer_creation_denials_have_zero_side_effects": True,
        "owner_routing_privacy_and_terminal_ledger_checked": True,
        "operation_scoped_customer_fixture_cleanup_zero": True,
    }


def _customer_actor_writer_ready(
    plan: dict[str, Any],
    *,
    scenario_id: str,
) -> dict[str, Any]:
    lifecycle = CUSTOMER_LIFECYCLE_MATRIX.get(scenario_id)
    if lifecycle is None:
        raise LiveMatrixError("customer actor lifecycle is unknown")
    role = lifecycle["webapp_writer"]
    observation = _writer_lease_observation(plan, role)
    if observation.get("local_active_with_witness_lease") is not True:
        raise LiveMatrixError("customer actor lifecycle writer has no witness lease")
    # Physical FI/IR locations share the logical WebApp authority.  The
    # Witness-bound writer state, not the legacy business server mode,
    # distinguishes their active/standby roles.
    expected_server = "iran"
    if observation.get("current_server") != expected_server:
        raise LiveMatrixError("customer actor lifecycle writer server differs")
    return {
        "role": role,
        "current_server": expected_server,
        "writer_epoch": int(observation["writer_epoch"]),
        "transition_id": str(observation["transition_id"]),
    }


def _validate_customer_pair(
    payload: dict[str, Any],
    *,
    actor_pair: str,
    policy: str,
) -> None:
    if (
        payload.get("actor_pair") != actor_pair
        or payload.get("execution_policy") != policy
        or payload.get("passed") is not True
        or payload.get("counterparty_privacy_preserved") is not True
    ):
        raise LiveMatrixError("customer actor pair did not pass its identity/privacy check")
    if policy == "positive_all_eligible_surfaces":
        if payload.get("result") != "eligible_surface_trade_completed":
            raise LiveMatrixError("customer positive actor pair result differs")
        for surface in ("webapp", "telegram"):
            evidence = payload.get(surface)
            if (
                payload.get(f"{surface}_status") != "success"
                or not isinstance(evidence, dict)
                or evidence.get("trade_count") != 1
                or evidence.get("offer_request_status_counts", {}).get("completed_trade") != 1
                or evidence.get("offer_request_count") != 1
                or not isinstance(evidence.get("offer_requests"), list)
                or not any(
                    isinstance(row, dict)
                    and row.get("requester_matches") is True
                    and row.get("actor_matches") is True
                    and row.get("result_status") == "completed_trade"
                    and row.get("resulting_trade") is True
                    for row in evidence["offer_requests"]
                )
            ):
                raise LiveMatrixError("customer positive actor evidence is incomplete")
        return
    if policy == "positive_webapp_tier2_request_telegram_denied":
        webapp = payload.get("webapp")
        telegram = payload.get("telegram")
        if (
            payload.get("result") != "webapp_trade_completed_and_telegram_request_denied"
            or payload.get("webapp_status") != "success"
            or payload.get("telegram_status") != "rejected"
            or not isinstance(webapp, dict)
            or not isinstance(telegram, dict)
            or webapp.get("trade_count") != 1
            or webapp.get("offer_request_status_counts", {}).get("completed_trade") != 1
            or telegram.get("trade_count") != 0
        ):
            raise LiveMatrixError("customer Tier-2 request routing evidence is incomplete")
        return
    if policy == "negative_tier2_offer_creation_denied":
        delta = payload.get("side_effect_delta")
        if (
            payload.get("result") != "tier2_offer_creation_denied_with_zero_mutation"
            or payload.get("webapp_status") != "rejected"
            or payload.get("telegram_status") != "rejected"
            or not isinstance(delta, dict)
            or set(delta) != {"notifications", "offer_requests", "offers", "publication_states", "trades"}
            or any(type(value) is not int or value != 0 for value in delta.values())
        ):
            raise LiveMatrixError("customer Tier-2 offer denial has side effects")
        return
    raise LiveMatrixError("customer actor execution policy is unknown")


def _validate_customer_actor_payload(
    payload: dict[str, Any],
    *,
    scenario_id: str,
    writer_role: str,
    prefix: str,
) -> None:
    lifecycle = CUSTOMER_LIFECYCLE_MATRIX.get(scenario_id)
    if lifecycle is None:
        raise LiveMatrixError("customer actor lifecycle is unknown")
    required = {
        "schema", "status", "scenario_id", "writer_role", "runtime_state",
        "server_mode", "prefix", "pair_count", "pairs", "cleanup",
    }
    if (
        set(payload) != required
        or payload.get("schema") != "three-site-full-matrix-customer-actor-probe-v1"
        or payload.get("status") != "passed"
        or payload.get("scenario_id") != scenario_id
        or payload.get("writer_role") != writer_role
        or payload.get("runtime_state") != lifecycle["runtime_state"]
        or payload.get("prefix") != prefix
        or payload.get("pair_count") != len(CUSTOMER_ACTOR_PAIR_POLICIES)
        or not isinstance(payload.get("pairs"), list)
        or not isinstance(payload.get("cleanup"), dict)
        or payload["cleanup"] != {"deleted_total": payload["cleanup"].get("deleted_total"), "residue_zero": True}
        or type(payload["cleanup"].get("deleted_total")) is not int
    ):
        raise LiveMatrixError("customer actor probe output is malformed")
    seen: set[str] = set()
    for item in payload["pairs"]:
        if not isinstance(item, dict):
            raise LiveMatrixError("customer actor pair output is malformed")
        actor_pair = str(item.get("actor_pair") or "")
        policy = CUSTOMER_ACTOR_PAIR_POLICIES.get(actor_pair)
        if policy is None or actor_pair in seen:
            raise LiveMatrixError("customer actor pair set is invalid")
        _validate_customer_pair(item, actor_pair=actor_pair, policy=policy)
        # Probe evidence is intentionally identity-minimized.  A mobile/phone
        # field here would make the privacy assertion self-contradictory.
        serialized = json.dumps(item, ensure_ascii=False, sort_keys=True).lower()
        if any(token in serialized for token in ("mobile_number", "phone_number", "phone")):
            raise LiveMatrixError("customer actor evidence contains private contact data")
        seen.add(actor_pair)
    if seen != set(CUSTOMER_ACTOR_PAIR_POLICIES):
        raise LiveMatrixError("customer actor probe did not execute exactly 17 pairs")


def _customer_actor_outcome(
    payload: dict[str, Any],
    *,
    scenario_id: str,
    writer_role: str,
) -> dict[str, Any]:
    _validate_customer_actor_payload(
        payload,
        scenario_id=scenario_id,
        writer_role=writer_role,
        prefix=str(payload.get("prefix") or ""),
    )
    policy_counts = {
        policy: sum(1 for value in CUSTOMER_ACTOR_PAIR_POLICIES.values() if value == policy)
        for policy in sorted(set(CUSTOMER_ACTOR_PAIR_POLICIES.values()))
    }
    return {
        "runtime_state": CUSTOMER_LIFECYCLE_MATRIX[scenario_id]["runtime_state"],
        "writer_role": writer_role,
        "exact_pair_count": len(CUSTOMER_ACTOR_PAIR_POLICIES),
        "policy_counts": policy_counts,
        "all_pair_policies_passed": True,
        "bounded_fixture_cleanup_residue_zero": True,
    }


def _run_customer_actor_matrix(
    args: Any,
    plan: dict[str, Any],
    scenario_id: str,
    *,
    observer: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ready_before = _customer_actor_writer_ready(plan, scenario_id=scenario_id)
    writer_role = str(ready_before["role"])
    prefix = _customer_actor_prefix(args, scenario_id, observer=observer)
    role = plan["_roles"][writer_role]
    if role.get("transport") == "object-storage-agent":
        control = run_role_agent_operation(
            writer_role,
            role,
            operation="customer_actor_matrix",
            context={
                "scenario_id": scenario_id,
                "prefix": prefix,
                "observer": observer,
            },
            attempt=1,
            timeout=3600,
        )
        result = control.get("result")
        payload = result.get("probe_payload") if isinstance(result, dict) else None
    else:
        result = run_compose_role_service(
            writer_role,
            role,
            service=ROLE_WORKLOAD_SERVICE[writer_role],
            command=[
                "/app/scripts/full_matrix_live/customer_actor_probe.py",
                "--scenario-id", scenario_id,
                "--writer-role", writer_role,
                "--prefix", prefix,
                "--allow-production-execution",
                "--allow-production-cleanup",
            ],
            timeout=1800,
        )
        try:
            payload = json.loads(result["stdout"], object_pairs_hook=strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveMatrixError("customer actor probe output is invalid") from exc
    if not isinstance(payload, dict):
        raise LiveMatrixError("customer actor probe did not return an object")
    _validate_customer_actor_payload(
        payload,
        scenario_id=scenario_id,
        writer_role=writer_role,
        prefix=prefix,
    )
    ready_after = _customer_actor_writer_ready(plan, scenario_id=scenario_id)
    if (
        ready_after["role"] != writer_role
        or ready_after["writer_epoch"] != ready_before["writer_epoch"]
        or ready_after["transition_id"] != ready_before["transition_id"]
    ):
        raise LiveMatrixError("customer actor matrix changed the live writer route")
    return _customer_actor_outcome(
        payload,
        scenario_id=scenario_id,
        writer_role=writer_role,
    ), {
        "writer_before": ready_before,
        "writer_after": ready_after,
        "probe": payload,
        "probe_sha256": hash_summary(payload),
    }


def _retain_customer_actor_evidence(
    args: Any,
    *,
    scenario_id: str,
    observations: dict[str, Any],
) -> list[dict[str, Any]]:
    probe = observations.get("probe")
    if not isinstance(probe, dict) or not isinstance(probe.get("pairs"), list):
        raise LiveMatrixError("customer actor independent probe evidence is missing")
    contracts = customer_actor_pair_contracts(scenario_id)
    retained: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in probe["pairs"]:
        if not isinstance(item, dict):
            raise LiveMatrixError("customer actor pair evidence is malformed")
        actor_pair = str(item.get("actor_pair") or "")
        assertion_name = customer_actor_pair_assertion_name(actor_pair)
        contract = contracts.get(assertion_name)
        if contract is None or assertion_name in seen:
            raise LiveMatrixError("customer actor evidence contract is incomplete")
        safe_pair = actor_pair.replace("__", "-" )
        artifact_path = Path(args.artifact_root) / f"{args.operation_id}-customer-{safe_pair}.json"
        if artifact_path.exists() or artifact_path.is_symlink():
            raise LiveMatrixError("customer actor evidence artifact already exists")
        raw = (json.dumps({
            "schema": "three-site-full-matrix-customer-actor-evidence-v1",
            "scenario_id": scenario_id,
            "assertion_name": assertion_name,
            "contract": contract,
            "observed_pair": item,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        write_secure_atomic_bytes(
            artifact_path,
            raw,
            label="Full Matrix customer actor-pair evidence",
            mode=0o600,
        )
        retained.append({
            "name": assertion_name,
            "contract": contract,
            "evidence": {
                "path": artifact_path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
            },
        })
        seen.add(assertion_name)
    if seen != set(contracts):
        raise LiveMatrixError("customer actor retained proof set differs")
    return retained


def _messenger_regression_prefix(args: Any, *, observer: bool) -> str:
    token = str(args.operation_id).replace("-", "")[:16]
    prefix = f"FMX_{token}_MESSENGER_{'ORACLE' if observer else 'DOER'}_"
    if re.fullmatch(r"FMX_[A-Za-z0-9_]{12,96}", prefix) is None:
        raise LiveMatrixError("messenger fixture prefix is unsafe")
    return prefix


def _validate_messenger_regression_payload(
    payload: dict[str, Any],
    *,
    prefix: str,
    scenario_id: str,
) -> None:
    if scenario_id not in MESSENGER_REGRESSION_LIVE_HANDLER_IDS:
        raise LiveMatrixError("messenger regression scenario is unsupported")
    required = {
        "schema", "status", "scenario_id", "role", "prefix", "writer_epoch", "observation", "cleanup"
    }
    if (
        set(payload) != required
        or payload.get("schema") != "three-site-full-matrix-messenger-regression-probe-v1"
        or payload.get("status") != "passed"
        or payload.get("scenario_id") != scenario_id
        or payload.get("role") != "webapp_fi"
        or payload.get("prefix") != prefix
        or type(payload.get("writer_epoch")) is not int
        or int(payload["writer_epoch"]) < 1
        or not isinstance(payload.get("observation"), dict)
        or not isinstance(payload.get("cleanup"), dict)
    ):
        raise LiveMatrixError("messenger regression probe output is malformed")
    observation = payload["observation"]
    cleanup = payload["cleanup"]
    expected_observation = {
        "uploaded_immutable_blob",
        "dr_file_intent_and_delivery_created",
        "sender_download_authorized",
        "recipient_download_authorized",
        "unrelated_user_denied",
    }
    if scenario_id == "notifications_webpush_messenger_files":
        expected_observation.add("notification_persisted_with_durable_webpush_fanout")
    expected_cleanup = {
        "active_fixture_rows_removed",
        "encrypted_blob_retention_owned_by_dr",
    }
    if (
        set(observation) != expected_observation
        or set(cleanup) != expected_cleanup
        or any(value is not True for value in observation.values())
        or any(value is not True for value in cleanup.values())
    ):
        raise LiveMatrixError("messenger regression probe did not satisfy its closed contract")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    if any(token in serialized for token in ("mobile_number", "phone_number", "phone", "token=")):
        raise LiveMatrixError("messenger regression evidence contains private material")


def _messenger_regression_outcome(
    payload: dict[str, Any],
    *,
    prefix: str,
    scenario_id: str,
) -> dict[str, bool]:
    _validate_messenger_regression_payload(payload, prefix=prefix, scenario_id=scenario_id)
    outcome = {
        "writer_fenced_immutable_blob_upload": True,
        "private_dr_blob_intent_and_delivery_created": True,
        "direct_messenger_file_access_is_authorized": True,
        "unrelated_messenger_file_access_is_denied": True,
        "synthetic_relational_rows_removed_without_blob_deletion": True,
    }
    if scenario_id == "notifications_webpush_messenger_files":
        outcome["notification_has_durable_webpush_effect_handoff"] = True
    return outcome


def _run_messenger_regression(
    args: Any,
    plan: dict[str, Any],
    *,
    scenario_id: str,
    observer: bool,
) -> tuple[dict[str, bool], dict[str, Any]]:
    before = _writer_lease_observation(plan, "webapp_fi")
    if (
        before.get("local_active_with_witness_lease") is not True
        or before.get("current_server") != "iran"
    ):
        raise LiveMatrixError("messenger regression requires WebApp-FI as the live Writer")
    prefix = _messenger_regression_prefix(args, observer=observer)
    role = plan["_roles"]["webapp_fi"]
    if role.get("transport") != "ssh":
        raise LiveMatrixError("messenger regression must use the pinned WebApp-FI transport")
    result = run_compose_role_service(
        "webapp_fi",
        role,
        service=ROLE_WORKLOAD_SERVICE["webapp_fi"],
        command=[
            "/app/scripts/full_matrix_live/messenger_regression_probe.py",
            "--scenario-id",
            scenario_id,
            "--prefix",
            prefix,
            "--allow-production-execution",
            "--allow-production-cleanup",
        ],
        timeout=1800,
    )
    try:
        payload = json.loads(result["stdout"], object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError("messenger regression probe output is invalid") from exc
    if not isinstance(payload, dict):
        raise LiveMatrixError("messenger regression probe did not return an object")
    _validate_messenger_regression_payload(payload, prefix=prefix, scenario_id=scenario_id)
    after = _writer_lease_observation(plan, "webapp_fi")
    if (
        after.get("local_active_with_witness_lease") is not True
        or after.get("current_server") != "iran"
        or int(after.get("writer_epoch") or 0) != int(before.get("writer_epoch") or 0)
        or after.get("transition_id") != before.get("transition_id")
    ):
        raise LiveMatrixError("messenger regression changed the live Writer route")
    return _messenger_regression_outcome(payload, prefix=prefix, scenario_id=scenario_id), {
        "writer_before": before,
        "writer_after": after,
        "probe": payload,
        "probe_sha256": hash_summary(payload),
    }


def _telegram_queue_regression_prefix(args: Any, *, observer: bool) -> str:
    token = str(args.operation_id).replace("-", "")[:16]
    prefix = f"FMX_{token}_QUEUE_{'ORACLE' if observer else 'DOER'}_"
    if re.fullmatch(r"FMX_[A-Za-z0-9_]{12,96}", prefix) is None:
        raise LiveMatrixError("Telegram queue fixture prefix is unsafe")
    return prefix


def _validate_telegram_queue_regression_payload(
    payload: dict[str, Any],
    *,
    campaign_id: str,
    prefix: str,
) -> None:
    if set(payload) != {
        "schema",
        "status",
        "scenario_id",
        "role",
        "prefix",
        "campaign_id",
        "run_id",
        "observation",
        "cleanup",
    }:
        raise LiveMatrixError("Telegram queue regression probe output is malformed")
    run_id = str(payload.get("run_id") or "")
    if (
        payload.get("schema")
        != "three-site-full-matrix-telegram-queue-regression-probe-v1"
        or payload.get("status") != "passed"
        or payload.get("scenario_id")
        != "queue_publication_edit_callback_private"
        or payload.get("role") != "bot_fi"
        or payload.get("prefix") != prefix
        or payload.get("campaign_id") != campaign_id
        or not run_id.startswith(f"full-matrix:{campaign_id}:")
        or not isinstance(payload.get("observation"), dict)
        or not isinstance(payload.get("cleanup"), dict)
    ):
        raise LiveMatrixError("Telegram queue regression probe identity differs")
    expected_observation = {
        "full_matrix_lane_is_reserved_and_operationally_invisible",
        "publication_edit_callback_and_private_jobs_enqueued",
        "each_fixture_job_claimed_under_lease_fence",
        "fake_provider_outcomes_applied_without_network_call",
        "all_fixture_jobs_terminal",
    }
    expected_cleanup = {
        "only_exact_reserved_fixture_rows_deleted",
        "fixture_residue_zero",
    }
    if (
        set(payload["observation"]) != expected_observation
        or set(payload["cleanup"]) != expected_cleanup
        or any(value is not True for value in payload["observation"].values())
        or any(value is not True for value in payload["cleanup"].values())
    ):
        raise LiveMatrixError("Telegram queue regression did not satisfy its contract")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    if any(token in serialized for token in ("bot_token", "token=", "phone", "chat_id")):
        raise LiveMatrixError("Telegram queue regression evidence contains private material")


def _telegram_queue_regression_outcome(
    payload: dict[str, Any],
    *,
    campaign_id: str,
    prefix: str,
) -> dict[str, bool]:
    _validate_telegram_queue_regression_payload(
        payload,
        campaign_id=campaign_id,
        prefix=prefix,
    )
    return {
        "full_matrix_queue_lane_isolated_from_operational_delivery": True,
        "publication_channel_edit_callback_and_private_queue_paths_persisted": True,
        "every_fixture_dispatch_is_lease_fenced": True,
        "provider_boundary_is_fake_and_network_free": True,
        "exact_fixture_cleanup_has_zero_residue": True,
    }


def _run_telegram_queue_regression(
    args: Any,
    plan: dict[str, Any],
    *,
    observer: bool,
) -> tuple[dict[str, bool], dict[str, Any]]:
    role = plan["_roles"]["bot_fi"]
    if role.get("transport") != "local":
        raise LiveMatrixError("Telegram queue regression must use the local Bot-FI role")
    prefix = _telegram_queue_regression_prefix(args, observer=observer)
    result = run_compose_role_service(
        "bot_fi",
        role,
        service=ROLE_WORKLOAD_SERVICE["bot_fi"],
        command=[
            "/app/scripts/full_matrix_live/telegram_queue_regression_probe.py",
            "--campaign-id",
            args.campaign_id,
            "--prefix",
            prefix,
            "--allow-production-execution",
            "--allow-production-cleanup",
        ],
        timeout=900,
    )
    try:
        payload = json.loads(result["stdout"], object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError("Telegram queue regression probe output is invalid") from exc
    if not isinstance(payload, dict):
        raise LiveMatrixError("Telegram queue regression probe did not return an object")
    outcome = _telegram_queue_regression_outcome(
        payload,
        campaign_id=args.campaign_id,
        prefix=prefix,
    )
    return outcome, {
        "probe": payload,
        "probe_sha256": hash_summary(payload),
        "role": "bot_fi",
        "observer": observer,
    }


def _application_regression_prefix(
    args: Any,
    scenario_id: str,
    *,
    observer: bool,
) -> str:
    if scenario_id not in APPLICATION_REGRESSION_LIVE_HANDLER_IDS:
        raise LiveMatrixError("application regression scenario is unsupported")
    token = str(args.operation_id).replace("-", "")[:16]
    scenario_token = re.sub(r"[^A-Za-z0-9]", "", scenario_id).upper()[:48]
    prefix = f"FMX_{token}_APP_{scenario_token}_{'ORACLE' if observer else 'DOER'}_"
    if re.fullmatch(r"FMX_[A-Za-z0-9_]{12,96}", prefix) is None:
        raise LiveMatrixError("application regression fixture prefix is unsafe")
    return prefix


def _application_regression_expected_observation(scenario_id: str) -> set[str]:
    values = {
        "market_trade_account_admin_regression": {
            "fixture_trade_created_by_real_router",
            "market_cursor_pages_exact",
            "trade_history_cursor_pages_exact",
            "account_identity_endpoint_authorized",
            "admin_listing_route_authorized",
        },
        "websocket_reconnect_and_cursor_reconcile": {
            "fixture_trade_created_by_real_router",
            "first_websocket_receives_exact_user_event",
            "reconnect_websocket_receives_new_exact_user_event",
            "market_cursor_pages_exact",
            "trade_history_cursor_pages_exact",
        },
    }
    expected = values.get(scenario_id)
    if expected is None:
        raise LiveMatrixError("application regression observation contract is incomplete")
    return expected


def _validate_application_regression_payload(
    payload: dict[str, Any],
    *,
    scenario_id: str,
    prefix: str,
) -> None:
    expected_observation = _application_regression_expected_observation(scenario_id)
    expected_cleanup = {
        "only_prefixed_fixture_rows_deleted",
        "fixture_residue_zero",
    }
    if (
        set(payload)
        != {
            "schema", "status", "scenario_id", "role", "prefix",
            "writer_epoch", "observation", "cleanup",
        }
        or payload.get("schema")
        != "three-site-full-matrix-application-regression-probe-v1"
        or payload.get("status") != "passed"
        or payload.get("scenario_id") != scenario_id
        or payload.get("role") != "webapp_fi"
        or payload.get("prefix") != prefix
        or type(payload.get("writer_epoch")) is not int
        or int(payload["writer_epoch"]) < 1
        or not isinstance(payload.get("observation"), dict)
        or not isinstance(payload.get("cleanup"), dict)
        or set(payload["observation"]) != expected_observation
        or set(payload["cleanup"]) != expected_cleanup
        or any(value is not True for value in payload["observation"].values())
        or any(value is not True for value in payload["cleanup"].values())
    ):
        raise LiveMatrixError("application regression probe did not satisfy its closed contract")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    if any(
        token in serialized
        for token in ("jwt", "authorization", "password", "phone", "chat_id", "session_id")
    ):
        raise LiveMatrixError("application regression evidence contains private material")


def _application_regression_outcome(
    payload: dict[str, Any],
    *,
    scenario_id: str,
    prefix: str,
) -> dict[str, bool]:
    _validate_application_regression_payload(
        payload,
        scenario_id=scenario_id,
        prefix=prefix,
    )
    if scenario_id == "market_trade_account_admin_regression":
        return {
            "real_writer_router_trade_visible_via_authenticated_market_history": True,
            "market_and_trade_cursor_contracts_exact": True,
            "account_identity_and_admin_routes_authorized_only_for_fixture_cohort": True,
            "bounded_application_fixture_cleanup_zero": True,
        }
    if scenario_id == "websocket_reconnect_and_cursor_reconcile":
        return {
            "two_independent_websocket_connections_received_only_their_exact_user_events": True,
            "market_and_trade_cursor_reconciliation_has_no_duplicate_page_item": True,
            "bounded_application_fixture_cleanup_zero": True,
        }
    raise LiveMatrixError("application regression outcome dispatch is incomplete")


def _run_application_regression(
    args: Any,
    plan: dict[str, Any],
    *,
    scenario_id: str,
    observer: bool,
) -> tuple[dict[str, bool], dict[str, Any]]:
    role = plan["_roles"]["webapp_fi"]
    if role.get("transport") != "ssh":
        raise LiveMatrixError("application regression must use the pinned WebApp-FI transport")
    before = _writer_lease_observation(plan, "webapp_fi")
    if (
        before.get("local_active_with_witness_lease") is not True
        or before.get("current_server") != "iran"
    ):
        raise LiveMatrixError("application regression requires WebApp-FI as the live Writer")
    prefix = _application_regression_prefix(args, scenario_id, observer=observer)
    result = run_compose_role_service(
        "webapp_fi",
        role,
        service=ROLE_WORKLOAD_SERVICE["webapp_fi"],
        command=[
            "/app/scripts/full_matrix_live/application_regression_probe.py",
            "--scenario-id", scenario_id,
            "--prefix", prefix,
            "--allow-production-execution",
            "--allow-production-cleanup",
        ],
        timeout=1800,
    )
    try:
        payload = json.loads(result["stdout"], object_pairs_hook=strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError("application regression probe output is invalid") from exc
    if not isinstance(payload, dict):
        raise LiveMatrixError("application regression probe did not return an object")
    _validate_application_regression_payload(
        payload,
        scenario_id=scenario_id,
        prefix=prefix,
    )
    if int(payload["writer_epoch"]) != int(before.get("writer_epoch") or 0):
        raise LiveMatrixError("application regression probe writer epoch differs")
    after = _writer_lease_observation(plan, "webapp_fi")
    if (
        after.get("local_active_with_witness_lease") is not True
        or after.get("current_server") != "iran"
        or int(after.get("writer_epoch") or 0) != int(before.get("writer_epoch") or 0)
        or after.get("transition_id") != before.get("transition_id")
    ):
        raise LiveMatrixError("application regression changed the live Writer route")
    convergence, states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    return _application_regression_outcome(
        payload,
        scenario_id=scenario_id,
        prefix=prefix,
    ), {
        "writer_before": before,
        "writer_after": after,
        "probe": payload,
        "probe_sha256": hash_summary(payload),
        "fresh_convergence": convergence,
        "fresh_convergence_states_sha256": hash_summary(states),
        "observer": observer,
    }


_SESSION_FAILOVER_SCHEMA = "three-site-full-matrix-session-failover-state-v1"
_SESSION_FAILOVER_ID = "session_failover_contract"
_SESSION_OBSERVATION = {
    "pre_promotion_session_accepted_after_ir_writer_activation",
    "post_promotion_websocket_reauthenticated_and_received_exact_event",
    "ir_writer_revoked_primary_session_fail_closed",
    "ir_writer_promoted_backup_session_and_authorized_it",
}
_SESSION_CLEANUP = {
    "only_prefixed_session_fixture_rows_deleted",
    "exact_session_blacklist_keys_removed",
    "fixture_residue_zero",
}


def _session_failover_prefix(args: Any, *, observer: bool) -> str:
    token = str(args.operation_id).replace("-", "")[:16]
    value = f"FMX_{token}_SESSIONFAILOVER_{'ORACLE' if observer else 'DOER'}_"
    if re.fullmatch(r"FMX_[A-Za-z0-9_]{12,96}", value) is None:
        raise LiveMatrixError("session failover fixture prefix is unsafe")
    return value


def _session_failover_state_path(args: Any, plan: dict[str, Any]) -> Path:
    operation_id = str(args.operation_id)
    if not re.fullmatch(r"[0-9a-f-]{36}", operation_id):
        raise LiveMatrixError("session failover operation identity is unsafe")
    root = Path(plan["_state_root"])
    return root / f"{operation_id}-session-failover.json"


def _fixture_from_prepare(payload: Any, *, prefix: str) -> dict[str, Any]:
    required = {
        "schema", "status", "mode", "role", "prefix", "writer_epoch",
        "observation", "fixture",
    }
    fixture_fields = {
        "user_id", "primary_session_id", "backup_session_id", "descriptor_sha256",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or payload.get("schema")
        != "three-site-full-matrix-cross-writer-session-probe-v1"
        or payload.get("status") != "passed"
        or payload.get("mode") != "prepare"
        or payload.get("role") != "webapp_fi"
        or payload.get("prefix") != prefix
        or type(payload.get("writer_epoch")) is not int
        or int(payload["writer_epoch"]) < 1
        or payload.get("observation")
        != {"pre_promotion_primary_session_authorized": True}
        or not isinstance(payload.get("fixture"), dict)
        or set(payload["fixture"]) != fixture_fields
        or type(payload["fixture"].get("user_id")) is not int
        or int(payload["fixture"]["user_id"]) < 1
        or any(
            not isinstance(payload["fixture"].get(name), str)
            for name in ("primary_session_id", "backup_session_id", "descriptor_sha256")
        )
        or payload["fixture"]["primary_session_id"] == payload["fixture"]["backup_session_id"]
        or re.fullmatch(r"[0-9a-f]{64}", payload["fixture"]["descriptor_sha256"])
        is None
    ):
        raise LiveMatrixError("FI session preparation proof is malformed")
    return {
        "prefix": prefix,
        "user_id": int(payload["fixture"]["user_id"]),
        "primary_session_id": str(payload["fixture"]["primary_session_id"]),
        "backup_session_id": str(payload["fixture"]["backup_session_id"]),
        "descriptor_sha256": str(payload["fixture"]["descriptor_sha256"]),
        "writer_epoch": int(payload["writer_epoch"]),
    }


def _prepare_session_fixture(
    args: Any,
    plan: dict[str, Any],
    *,
    observer: bool,
) -> dict[str, Any]:
    prefix = _session_failover_prefix(args, observer=observer)
    role = plan["_roles"]["webapp_fi"]
    result = run_compose_role_service(
        "webapp_fi",
        role,
        service=ROLE_WORKLOAD_SERVICE["webapp_fi"],
        command=[
            "/app/scripts/full_matrix_live/cross_writer_session_probe.py",
            "--mode", "prepare",
            "--prefix", prefix,
            "--allow-production-execution",
            "--allow-production-cleanup",
        ],
        timeout=1800,
    )
    try:
        payload = json.loads(result["stdout"], object_pairs_hook=strict_object)
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError("FI session preparation output is invalid") from exc
    return _fixture_from_prepare(payload, prefix=prefix)


def _session_state(args: Any, plan: dict[str, Any]) -> dict[str, Any]:
    path = _session_failover_state_path(args, plan)
    try:
        raw = safe_read(
            path,
            label="Full Matrix session failover private state",
            owner_only=True,
            max_size=128 * 1024,
        )
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    except Exception as exc:
        raise LiveMatrixError("session failover private state is invalid") from exc
    required = {
        "schema", "operation_id", "scenario_id", "release_sha", "phase", "fixtures",
        "promotion", "doer_verification",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schema") != _SESSION_FAILOVER_SCHEMA
        or value.get("operation_id") != args.operation_id
        or value.get("scenario_id") != _SESSION_FAILOVER_ID
        or value.get("release_sha") != plan["release_sha"]
        or value.get("phase") not in {"prepared", "promoted", "doer_verified"}
        or not isinstance(value.get("fixtures"), dict)
        or set(value["fixtures"]) != {"doer", "oracle"}
        or not isinstance(value.get("promotion"), dict)
        or value["promotion"].get("status") != "completed"
        or not isinstance(value.get("doer_verification"), dict)
    ):
        raise LiveMatrixError("session failover private state differs from campaign")
    for name, observer in (("doer", False), ("oracle", True)):
        fixture = value["fixtures"].get(name)
        expected_prefix = _session_failover_prefix(args, observer=observer)
        if (
            not isinstance(fixture, dict)
            or fixture.get("prefix") != expected_prefix
            or type(fixture.get("user_id")) is not int
            or int(fixture["user_id"]) < 1
            or any(
                not isinstance(fixture.get(field), str)
                for field in ("primary_session_id", "backup_session_id", "descriptor_sha256")
            )
            or re.fullmatch(r"[0-9a-f]{64}", str(fixture.get("descriptor_sha256") or ""))
            is None
        ):
            raise LiveMatrixError("session failover fixture state is invalid")
    return value


def _write_session_state(args: Any, plan: dict[str, Any], state: dict[str, Any]) -> None:
    path = _session_failover_state_path(args, plan)
    raw = (
        json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    write_secure_atomic_bytes(
        path,
        raw,
        label="Full Matrix session failover private state",
        mode=0o600,
        max_size=128 * 1024,
    )


def _remove_session_state(args: Any, plan: dict[str, Any]) -> None:
    path = _session_failover_state_path(args, plan)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LiveMatrixError("session failover private state disappeared before cleanup") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise LiveMatrixError("session failover private state is unsafe to remove")
    path.unlink()


def _verify_session_fixture_on_ir(
    plan: dict[str, Any],
    *,
    fixture: dict[str, Any],
    observer: bool,
) -> dict[str, Any]:
    control = run_role_agent_operation(
        "webapp_ir",
        plan["_roles"]["webapp_ir"],
        operation="cross_writer_session_verify",
        context={
            "prefix": fixture["prefix"],
            "user_id": fixture["user_id"],
            "primary_session_id": fixture["primary_session_id"],
            "backup_session_id": fixture["backup_session_id"],
            "descriptor_sha256": fixture["descriptor_sha256"],
            "observer": observer,
        },
        attempt=1,
        timeout=1800,
    )
    envelope = control.get("result")
    result = envelope.get("result") if isinstance(envelope, dict) else None
    payload = result.get("probe_payload") if isinstance(result, dict) else None
    if (
        not isinstance(envelope, dict)
        or envelope.get("schema") != "three-site-full-matrix-site-agent-result-v1"
        or envelope.get("status") != "passed"
        or envelope.get("role") != "webapp_ir"
        or envelope.get("operation") != "cross_writer_session_verify"
        or not isinstance(result, dict)
        or result.get("status") != "passed"
        or not isinstance(payload, dict)
        or payload.get("descriptor_sha256") != fixture["descriptor_sha256"]
        or payload.get("prefix") != fixture["prefix"]
        or payload.get("role") != "webapp_ir"
        or type(payload.get("writer_epoch")) is not int
        or set(payload.get("observation") or {}) != _SESSION_OBSERVATION
        or any(value is not True for value in payload["observation"].values())
        or set(payload.get("cleanup") or {}) != _SESSION_CLEANUP
        or any(value is not True for value in payload["cleanup"].values())
    ):
        raise LiveMatrixError("IR session failover proof is incomplete")
    return {
        "writer_epoch": int(payload["writer_epoch"]),
        "descriptor_sha256": str(payload["descriptor_sha256"]),
        "observation": dict(payload["observation"]),
        "cleanup": dict(payload["cleanup"]),
    }


def _session_failover_doer(args: Any, plan: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    path = _session_failover_state_path(args, plan)
    if path.exists():
        state = _session_state(args, plan)
        if state["phase"] != "doer_verified":
            raise LiveMatrixError("session failover retained state needs safe recovery before replay")
        return {
            "two_private_fi_session_fixtures_prepared": True,
            "schedule_bound_promotion_to_ir_completed": True,
            "doer_session_continuity_proved_on_ir": True,
        }, {
            "promotion": {
                key: state["promotion"][key]
                for key in ("operation_id", "plan_hash", "writer_epoch_before", "writer_epoch_after")
            },
            "doer_verification": dict(state["doer_verification"]),
            "private_fixture_state_retained": True,
        }
    writer = _writer_lease_observation(plan, "webapp_fi")
    if writer.get("active_site") != "webapp_fi" or writer.get("local_active_with_witness_lease") is not True:
        raise LiveMatrixError("session failover requires WA-FI as the active Witness Writer")
    fixtures = {
        "doer": _prepare_session_fixture(args, plan, observer=False),
        "oracle": _prepare_session_fixture(args, plan, observer=True),
    }
    prepared = {
        "schema": _SESSION_FAILOVER_SCHEMA,
        "operation_id": args.operation_id,
        "scenario_id": _SESSION_FAILOVER_ID,
        "release_sha": plan["release_sha"],
        "phase": "prepared",
        "fixtures": fixtures,
        "promotion": {},
        "doer_verification": {},
    }
    _write_session_state(args, plan, prepared)
    convergence, convergence_states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    promotion = execute_transition(
        plan,
        scenario_id=_SESSION_FAILOVER_ID,
        iteration=args.iteration,
        action="promote_ir",
    )
    if promotion.get("status") != "completed":
        raise LiveMatrixError("session failover promotion did not complete")
    promoted = {**prepared, "phase": "promoted", "promotion": promotion}
    _write_session_state(args, plan, promoted)
    doer_proof = _verify_session_fixture_on_ir(
        plan,
        fixture=fixtures["doer"],
        observer=False,
    )
    completed = {**promoted, "phase": "doer_verified", "doer_verification": doer_proof}
    _write_session_state(args, plan, completed)
    return {
        "two_private_fi_session_fixtures_prepared": True,
        "schedule_bound_promotion_to_ir_completed": True,
        "doer_session_continuity_proved_on_ir": True,
    }, {
        "writer_before": writer,
        "pre_promotion_convergence": convergence,
        "pre_promotion_convergence_states_sha256": hash_summary(convergence_states),
        "promotion": {
            key: promotion[key]
            for key in ("operation_id", "plan_hash", "writer_epoch_before", "writer_epoch_after")
        },
        "doer_verification": doer_proof,
        "private_fixture_state_retained": True,
    }


def _session_failover_oracle(args: Any, plan: dict[str, Any]) -> tuple[dict[str, bool], dict[str, Any]]:
    state = _session_state(args, plan)
    if state["phase"] != "doer_verified":
        raise LiveMatrixError("session failover oracle requires the completed doer proof")
    writer = _writer_lease_observation(plan, "webapp_ir")
    if writer.get("active_site") != "webapp_ir" or writer.get("local_active_with_witness_lease") is not True:
        raise LiveMatrixError("session failover oracle requires WA-IR as active Witness Writer")
    oracle_proof = _verify_session_fixture_on_ir(
        plan,
        fixture=state["fixtures"]["oracle"],
        observer=True,
    )
    convergence, convergence_states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    failback = execute_transition(
        plan,
        scenario_id=_SESSION_FAILOVER_ID,
        iteration=args.iteration,
        action="failback_fi",
    )
    if failback.get("status") != "completed":
        raise LiveMatrixError("session failover failback did not complete")
    final_convergence, final_states = _wait_for_business_convergence(plan, timeout_seconds=900.0)
    final_writer = _writer_lease_observation(plan, "webapp_fi")
    if (
        final_writer.get("active_site") != "webapp_fi"
        or final_writer.get("local_active_with_witness_lease") is not True
        or int(final_writer.get("writer_epoch") or 0) != int(failback["writer_epoch_after"])
    ):
        raise LiveMatrixError("session failover did not restore WA-FI as the Witness Writer")
    _remove_session_state(args, plan)
    return {
        "two_private_fi_session_fixtures_prepared": True,
        "schedule_bound_promotion_to_ir_completed": True,
        "doer_session_continuity_proved_on_ir": True,
        "oracle_session_continuity_proved_on_ir": True,
        "schedule_bound_failback_to_fi_completed": True,
        "private_session_fixture_residue_zero": True,
    }, {
        "writer_ir_before": writer,
        "doer_verification": dict(state["doer_verification"]),
        "oracle_verification": oracle_proof,
        "pre_failback_convergence": convergence,
        "pre_failback_convergence_states_sha256": hash_summary(convergence_states),
        "failback": {
            key: failback[key]
            for key in ("operation_id", "plan_hash", "writer_epoch_before", "writer_epoch_after")
        },
        "final_convergence": final_convergence,
        "final_convergence_states_sha256": hash_summary(final_states),
        "final_writer_fi": final_writer,
        "private_fixture_state_removed": True,
    }


def execute_scenario(
    args: Any,
    plan: dict[str, Any],
    recipe: Recipe,
) -> dict[str, Any]:
    if recipe.scenario_id not in IMPLEMENTED_HANDLER_IDS or not recipe.implemented:
        raise LiveMatrixError("live recipe has no enabled exact handler")
    if recipe.scenario_id == "fresh_main_queue_dr_histories_equal":
        observations = _migration_states(plan, observer=False)
        outcome = _fresh_history_outcome(observations)
        if outcome["all_three_database_heads_equal"] is not True:
            raise LiveMatrixError("live migration histories/schema are not equal")
        contract = {
            "release_owned_migration_heads": True,
            "three_live_database_heads_exact": True,
            "three_live_database_schema_fingerprints_equal": True,
        }
    elif recipe.scenario_id == "legacy_staging_clone_migrated":
        outcome, observations = _legacy_clone_observation(
            plan,
            observer=False,
        )
        contract = {
            "two_legacy_authorities_backup_bound": True,
            "webapp_ir_cloned_from_webapp_fi": True,
            "witness_empty_seed": True,
            "four_role_global_commit_verified": True,
            "live_migration_histories_equal": True,
        }
    elif recipe.scenario_id == "least_privilege_roles_attested":
        observations = _observer_states(plan)
        outcome = _privilege_outcome(observations)
        if outcome["least_privilege_roles_attested"] is not True:
            raise LiveMatrixError("live observer database roles are overprivileged")
        contract = {
            "dedicated_observer_identity_per_database": True,
            "select_only_table_grants": True,
            "role_escalation_denied": True,
            "database_creation_denied": True,
            "rls_bypass_denied": True,
        }
    elif recipe.scenario_id == "set_role_and_cross_role_access_denied":
        observations = _observer_states(plan)
        outcome = _privilege_outcome(observations)
        if outcome["least_privilege_roles_attested"] is not True:
            raise LiveMatrixError("cross-role escalation is not denied")
        contract = {
            "three_dedicated_observer_identities": True,
            "cross_role_membership_denied": True,
            "database_creation_denied": True,
            "rls_bypass_denied": True,
            "only_select_table_grants": True,
        }
    elif recipe.scenario_id == "four_role_identity_isolated":
        observations = collect_all_host_snapshots(plan, args.release_sha)
        outcome = _four_role_outcome(plan, observations)
        if outcome["four_role_identity_isolated"] is not True:
            raise LiveMatrixError("four live role identities are not isolated")
        contract = {
            "four_distinct_machine_identities": True,
            "four_distinct_compose_projects": True,
            "four_distinct_host_storage_identities": True,
        }
    elif recipe.scenario_id == "backup_restore_rehearsed":
        outcome, observations = _backup_restore_execute(args, plan)
        contract = {
            "live_custom_format_backup": True,
            "two_independent_temporary_database_restores": True,
            "restored_schema_byte_comparison": True,
            "restored_data_byte_comparison": True,
            "operation_bound_cleanup_state": True,
        }
    elif recipe.scenario_id == "legacy_rollback_rehearsed":
        outcome, observations = _legacy_rollback_execute(args, plan)
        contract = {
            "live_database_never_downgraded": True,
            "isolated_clone_downgrade_forward_cycle": True,
            "release_head_restored": True,
            "schema_and_data_byte_equal_after_forward": True,
            "operation_bound_cleanup_state": True,
        }
    elif recipe.scenario_id in MIGRATION_FIXTURE_IDS:
        outcome, observations = _migration_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=False,
        )
        contract = _migration_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in COMBINED_WORKLOAD_LIVE_IDS:
        outcome, observations = _combined_workload_observation(
            args,
            plan,
            recipe.scenario_id,
        )
        contract = _combined_workload_contract(recipe.scenario_id)
    elif recipe.scenario_id in CUSTOMER_ACTOR_LIVE_HANDLER_IDS:
        outcome, observations = _run_customer_actor_matrix(
            args,
            plan,
            recipe.scenario_id,
            observer=False,
        )
        contract = _customer_actor_contract(recipe.scenario_id)
    elif recipe.scenario_id in MESSENGER_REGRESSION_LIVE_HANDLER_IDS:
        outcome, observations = _run_messenger_regression(
            args,
            plan,
            scenario_id=recipe.scenario_id,
            observer=False,
        )
        contract = {
            "writer_fenced_immutable_blob_upload": True,
            "private_dr_blob_intent_and_delivery_created": True,
            "direct_messenger_file_access_is_authorized": True,
            "unrelated_messenger_file_access_is_denied": True,
            "synthetic_relational_rows_removed_without_blob_deletion": True,
        }
        if recipe.scenario_id == "notifications_webpush_messenger_files":
            contract["notification_has_durable_webpush_effect_handoff"] = True
    elif recipe.scenario_id in TELEGRAM_QUEUE_REGRESSION_LIVE_HANDLER_IDS:
        outcome, observations = _run_telegram_queue_regression(
            args,
            plan,
            observer=False,
        )
        contract = {
            "full_matrix_queue_lane_isolated_from_operational_delivery": True,
            "publication_channel_edit_callback_and_private_queue_paths_persisted": True,
            "every_fixture_dispatch_is_lease_fenced": True,
            "provider_boundary_is_fake_and_network_free": True,
            "exact_fixture_cleanup_has_zero_residue": True,
        }
    elif recipe.scenario_id in APPLICATION_REGRESSION_LIVE_HANDLER_IDS:
        outcome, observations = _run_application_regression(
            args,
            plan,
            scenario_id=recipe.scenario_id,
            observer=False,
        )
        contract = dict(outcome)
    elif recipe.scenario_id == _SESSION_FAILOVER_ID:
        outcome, observations = _session_failover_doer(args, plan)
        contract = {
            "two_private_session_fixtures_never_enter_public_evidence": True,
            "fi_to_ir_transition_uses_schedule_bound_witness_receipt": True,
            "wa_ir_mutations_use_encrypted_object_storage_pull_only": True,
            "doer_and_oracle_session_continuity_are_separately_proved": True,
            "final_failback_and_private_fixture_cleanup_are_oracle_owned": True,
        }
    elif recipe.scenario_id in RECOVERY_TIMING_LIVE_HANDLER_IDS:
        outcome, observations = _run_recovery_timing_cycle(args, plan, label="doer")
        contract = {
            "two_closed_delivery_flaps_are_object_storage_pull_bound": True,
            "wa_ir_remains_the_only_witness_writer_and_public_origin": True,
            "durable_backlog_and_live_drain_overlap_are_journal_proved": True,
            "failure_recovery_resumes_then_cleans_exact_private_fixtures": True,
            "doer_and_oracle_execute_separate_recovery_cycles": True,
        }
    elif recipe.scenario_id in ONE_HOUR_BACKLOG_LIVE_HANDLER_IDS:
        outcome, observations = _run_one_hour_backlog_cycle(args, plan, label="doer")
        contract = {
            "sixty_minute_paused_delivery_window_is_durable_and_recoverable": True,
            "twenty_bounded_ir_writer_batches_exceed_one_rps_in_aggregate": True,
            "all_batch_backlog_receipts_and_final_live_routes_are_journal_proved": True,
            "wa_ir_pull_only_control_and_exact_fixture_cleanup": True,
            "doer_and_oracle_execute_separate_one_hour_cycles": True,
        }
    elif recipe.scenario_id in ENDURANCE_LIVE_HANDLER_IDS:
        outcome, observations = _run_endurance_cycle(args, plan)
        contract = {
            "twenty_four_hour_monotonic_window_has_288_durable_samples": True,
            "bounded_real_fi_writer_ingress_converges_every_five_minutes": True,
            "writer_epoch_and_all_managed_fault_counts_remain_safe": True,
            "four_host_storage_and_database_growth_are_hard_bounded": True,
            "oracle_reopens_journal_then_makes_fresh_post_window_observation": True,
        }
    elif recipe.scenario_id in DESTRUCTIVE_WITNESS_HANDLER_IDS:
        outcome, observations = _run_witness_pause(args, plan)
        contract = {
            "only_campaign_bound_reversible_witness_power_actions_are_used": True,
            "both_webapp_sites_fence_after_witness_vm_pause": True,
            "no_ir_promotion_occurs_without_national_cutoff": True,
            "witness_power_on_and_exact_fi_writer_recovery_are_proved": True,
            "oracle_performs_a_fresh_provider_writer_and_ingress_observation": True,
        }
    elif recipe.scenario_id in DESTRUCTIVE_FI_HOST_LOSS_HANDLER_IDS:
        outcome, observations = _run_fi_host_loss(args, plan)
        contract = {
            "only_campaign_bound_reversible_fi_power_actions_are_used": True,
            "wa_ir_is_observed_only_through_its_pull_agent_while_fi_is_down": True,
            "no_ir_promotion_occurs_without_national_cutoff": True,
            "fi_power_on_and_exact_writer_recovery_are_proved": True,
            "oracle_performs_a_fresh_provider_writer_and_ingress_observation": True,
        }
    elif recipe.scenario_id in DESTRUCTIVE_IR_ACTIVE_ORIGIN_LOSS_HANDLER_IDS:
        outcome, observations = _run_ir_active_origin_loss(args, plan)
        contract = {
            "only_campaign_bound_reversible_ir_power_actions_are_used": True,
            "wa_fi_remains_fenced_while_ir_is_the_recorded_writer": True,
            "public_ingress_fails_closed_without_substitute_origin": True,
            "ir_power_on_restores_the_exact_active_epoch": True,
            "oracle_performs_a_fresh_provider_writer_and_ingress_observation": True,
        }
    elif recipe.scenario_id in DESTRUCTIVE_FI_RECOVERY_HUB_LOSS_HANDLER_IDS:
        outcome, observations = _run_fi_recovery_hub_loss(args, plan)
        contract = {
            "only_campaign_bound_reversible_fi_power_actions_are_used": True,
            "wa_ir_is_observed_only_through_its_pull_agent_while_fi_is_down": True,
            "fi_hub_loss_never_changes_the_ir_writer_or_public_origin": True,
            "fi_hub_rejoin_reconverges_without_new_direct_bot_to_ir_path": True,
            "oracle_performs_a_fresh_provider_writer_and_ingress_observation": True,
        }
    elif recipe.scenario_id == "power_loss_between_fence_and_enable":
        outcome, observations = _run_power_loss_cutpoint(args, plan)
        contract = {
            "durable_source_fence_and_connection_drain_precede_fi_power_loss": True,
            "same_schedule_bound_jit_plan_resumes_after_source_power_loss": True,
            "wa_ir_target_enable_uses_encrypted_object_storage_pull_only": True,
            "exact_next_ir_writer_epoch_and_ingress_are_proved": True,
            "fi_rejoins_as_standby_before_following_ir_active_scenarios": True,
        }
    elif recipe.scenario_id in DESTRUCTIVE_CAPACITY_HANDLER_IDS:
        outcome, observations = _run_capacity_fault(args, plan)
        contract = {
            "dedicated_mount_capacity_is_reserved_without_volume_mutation": True,
            "writer_fence_rejects_unsafe_http_write_before_data_plane_error": True,
            "wal_event_redis_blob_planes_are_on_the_same_guarded_mount": True,
            "reserve_cleanup_restores_headroom_before_marker_removal": True,
            "oracle_rechecks_zero_capacity_fault_residue_and_writer_convergence": True,
        }
    elif recipe.scenario_id in TIMING_LIVE_HANDLER_IDS:
        outcome, observations = _timing_live_observation(
            args,
            plan,
            recipe.scenario_id,
        )
        contract = {
            "bounded_real_business_event_ingress": True,
            "three_independent_ntp_clock_observations": True,
            "journal_correlated_per_hop_timing": True,
            "operation_bound_cleanup_state": True,
        }
    elif recipe.scenario_id in INGRESS_LIVE_HANDLER_IDS:
        outcome, observations = _ingress_route_observation(plan)
        contract = _ingress_route_contract()
    elif recipe.scenario_id in ARTIFACT_ANCHOR_HANDLER_IDS:
        outcome, observations = _artifact_anchor_observation(args, plan)
        contract = _artifact_anchor_contract()
    elif recipe.scenario_id in CDN_LIVE_HANDLER_IDS:
        outcome, observations = _cdn_dynamic_cache_observation(plan)
        contract = _cdn_dynamic_cache_contract()
    elif recipe.scenario_id in CANONICAL_INGRESS_LIVE_HANDLER_IDS:
        outcome, observations = _canonical_ingress_observation(plan)
        contract = _canonical_ingress_contract()
    elif recipe.scenario_id in REPEATABILITY_LIVE_HANDLER_IDS:
        outcome, observations = _repeatability_observation(
            args,
            plan,
            allow_write=True,
        )
        contract = _repeatability_contract()
    elif recipe.scenario_id in FINAL_WRITER_HANDLER_IDS:
        outcome, observations = _final_writer_route_observation(args, plan)
        contract = {
            "fresh_three_database_convergence_before_route_assertion": True,
            "webapp_fi_is_the_only_witness_leased_writer": True,
            "webapp_ir_is_fenced_standby": True,
            "writer_epoch_transition_and_lease_replicated_exactly": True,
            "all_managed_fault_residue_zero": True,
        }
    elif recipe.scenario_id == "iran_international_cutoff_promotes_ir":
        outcome, observations = _promote_ir_lifecycle(args, plan)
        contract = {
            "exact_scheduled_promotion_is_witness_receipt_bound": True,
            "fresh_isolated_connectivity_is_required_before_promotion": True,
            "wa_ir_mutations_use_encrypted_object_storage_pull_only": True,
            "writer_and_public_ingress_are_independently_reobserved": True,
            "ir_active_checkpoint_is_private_owner_only_and_iteration_bound": True,
        }
    elif recipe.scenario_id == "fi_epoch_reacquire_and_route_switch":
        outcome, observations = _failback_fi_lifecycle(args, plan)
        contract = {
            "exact_scheduled_failback_is_witness_receipt_bound": True,
            "wa_ir_source_drain_and_fence_use_encrypted_object_storage_pull_only": True,
            "writer_and_public_ingress_are_independently_reobserved": True,
            "ir_active_checkpoint_is_removed_only_after_verified_fi_failback": True,
        }
    elif recipe.scenario_id in DR_POLICY_FIXTURE_IDS:
        outcome, observations = _dr_policy_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=False,
        )
        contract = _dr_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in DR_FAULT_POLICY_FIXTURE_IDS:
        outcome, observations = _dr_fault_policy_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=False,
        )
        contract = _dr_fault_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in FAILOVER_FAULT_POLICY_FIXTURE_IDS:
        outcome, observations = _failover_fault_policy_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=False,
        )
        contract = _failover_fault_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in RECOVERY_POLICY_FIXTURE_IDS:
        outcome, observations = _recovery_policy_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=False,
        )
        contract = _recovery_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in RUNTIME_POLICY_FIXTURE_IDS:
        outcome, observations = _runtime_policy_fixture_observation(
            args,
            plan,
            recipe.scenario_id,
        )
        contract = _runtime_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in RELEASE_TRANSITION_POLICY_FIXTURE_IDS:
        outcome, observations = _release_transition_policy_observation(
            plan,
            recipe.scenario_id,
            observer=False,
        )
        contract = _release_transition_policy_contract(recipe.scenario_id)
    elif recipe.scenario_id in QUEUE_POLICY_FIXTURE_IDS:
        outcome, observations = _queue_policy_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=False,
        )
        contract = _queue_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in SECURITY_POLICY_FIXTURE_IDS:
        outcome, observations = _security_policy_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=False,
        )
        contract = _security_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id == "cross_service_secret_boundaries":
        outcome, observations = _secret_boundary_observation(args, plan)
        contract = {
            "release_compose_secret_allowlist_verified": True,
            "three_independent_observer_invocations": True,
            "four_live_role_bundles_release_bound": True,
            "secret_values_never_emitted": True,
        }
    elif recipe.scenario_id in CONVERGENCE_HANDLER_IDS:
        observations = _convergence_states(plan)
        outcome = _convergence_outcome(
            observations,
            scenario_id=recipe.scenario_id,
        )
        contract = _convergence_contract(recipe.scenario_id)
    elif recipe.scenario_id in {
        "production_host_domain_bucket_untouched",
        "production_boundaries_reverified",
    }:
        outcome, observations = _production_boundary_observation(args, plan)
        contract = {
            "fresh_role_identities_checked": True,
            "production_hosts_excluded": True,
            "production_volumes_excluded": True,
            "production_buckets_excluded": True,
            "production_domains_not_targeted": True,
        }
    elif recipe.scenario_id in CLEANUP_LIVE_HANDLER_IDS:
        outcome, observations = _cleanup_live_observation(
            args,
            plan,
            recipe.scenario_id,
        )
        contract = _cleanup_live_contract(recipe.scenario_id)
    else:
        raise LiveMatrixError("implemented recipe dispatch is incomplete")
    return {
        "recipe": asdict(recipe),
        "recipe_contract": contract,
        "recipe_contract_sha256": hash_summary(contract),
        "expected_outcome": outcome,
        "doer_observations": observations,
        "doer_observations_sha256": hash_summary(observations),
    }


def verify_scenario(
    args: Any,
    plan: dict[str, Any],
    recipe: Recipe,
    runner: dict[str, Any],
) -> dict[str, Any]:
    if recipe.scenario_id not in IMPLEMENTED_HANDLER_IDS or not recipe.implemented:
        raise LiveMatrixError("live recipe has no enabled exact oracle")
    retained_contract = runner.get("recipe_contract")
    retained_expected = runner.get("expected_outcome")
    if (
        not isinstance(retained_contract, dict)
        or not isinstance(retained_expected, dict)
        or runner.get("recipe_contract_sha256") != hash_summary(retained_contract)
        or runner.get("doer_observations_sha256")
        != hash_summary(runner.get("doer_observations"))
    ):
        raise LiveMatrixError("retained recipe evidence is incomplete")
    if recipe.scenario_id == "fresh_main_queue_dr_histories_equal":
        independent = _migration_states(plan, observer=True)
        observed = _fresh_history_outcome(independent)
        contract = {
            "release_owned_migration_heads": True,
            "three_live_database_heads_exact": True,
            "three_live_database_schema_fingerprints_equal": True,
        }
    elif recipe.scenario_id == "legacy_staging_clone_migrated":
        observed, independent = _legacy_clone_observation(
            plan,
            observer=True,
        )
        contract = {
            "two_legacy_authorities_backup_bound": True,
            "webapp_ir_cloned_from_webapp_fi": True,
            "witness_empty_seed": True,
            "four_role_global_commit_verified": True,
            "live_migration_histories_equal": True,
        }
    elif recipe.scenario_id == "least_privilege_roles_attested":
        independent = _observer_states(plan)
        observed = _privilege_outcome(independent)
        contract = {
            "dedicated_observer_identity_per_database": True,
            "select_only_table_grants": True,
            "role_escalation_denied": True,
            "database_creation_denied": True,
            "rls_bypass_denied": True,
        }
    elif recipe.scenario_id == "set_role_and_cross_role_access_denied":
        independent = _observer_states(plan)
        observed = _privilege_outcome(independent)
        contract = {
            "three_dedicated_observer_identities": True,
            "cross_role_membership_denied": True,
            "database_creation_denied": True,
            "rls_bypass_denied": True,
            "only_select_table_grants": True,
        }
    elif recipe.scenario_id == "four_role_identity_isolated":
        independent = collect_all_host_snapshots(plan, args.release_sha)
        observed = _four_role_outcome(plan, independent)
        contract = {
            "four_distinct_machine_identities": True,
            "four_distinct_compose_projects": True,
            "four_distinct_host_storage_identities": True,
        }
    elif recipe.scenario_id == "backup_restore_rehearsed":
        observed, independent = _backup_restore_verify(args, plan, runner)
        contract = {
            "live_custom_format_backup": True,
            "two_independent_temporary_database_restores": True,
            "restored_schema_byte_comparison": True,
            "restored_data_byte_comparison": True,
            "operation_bound_cleanup_state": True,
        }
    elif recipe.scenario_id == "legacy_rollback_rehearsed":
        observed, independent = _legacy_rollback_verify(args, plan, runner)
        contract = {
            "live_database_never_downgraded": True,
            "isolated_clone_downgrade_forward_cycle": True,
            "release_head_restored": True,
            "schema_and_data_byte_equal_after_forward": True,
            "operation_bound_cleanup_state": True,
        }
    elif recipe.scenario_id in MIGRATION_FIXTURE_IDS:
        observed, independent = _migration_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=True,
        )
        contract = _migration_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in COMBINED_WORKLOAD_LIVE_IDS:
        observed, independent = _combined_workload_verify(
            args,
            plan,
            recipe.scenario_id,
            runner,
        )
        contract = _combined_workload_contract(recipe.scenario_id)
    elif recipe.scenario_id in CUSTOMER_ACTOR_LIVE_HANDLER_IDS:
        observed, independent = _run_customer_actor_matrix(
            args,
            plan,
            recipe.scenario_id,
            observer=True,
        )
        contract = _customer_actor_contract(recipe.scenario_id)
    elif recipe.scenario_id in MESSENGER_REGRESSION_LIVE_HANDLER_IDS:
        observed, independent = _run_messenger_regression(
            args,
            plan,
            scenario_id=recipe.scenario_id,
            observer=True,
        )
        contract = {
            "writer_fenced_immutable_blob_upload": True,
            "private_dr_blob_intent_and_delivery_created": True,
            "direct_messenger_file_access_is_authorized": True,
            "unrelated_messenger_file_access_is_denied": True,
            "synthetic_relational_rows_removed_without_blob_deletion": True,
        }
        if recipe.scenario_id == "notifications_webpush_messenger_files":
            contract["notification_has_durable_webpush_effect_handoff"] = True
    elif recipe.scenario_id in TELEGRAM_QUEUE_REGRESSION_LIVE_HANDLER_IDS:
        observed, independent = _run_telegram_queue_regression(
            args,
            plan,
            observer=True,
        )
        contract = {
            "full_matrix_queue_lane_isolated_from_operational_delivery": True,
            "publication_channel_edit_callback_and_private_queue_paths_persisted": True,
            "every_fixture_dispatch_is_lease_fenced": True,
            "provider_boundary_is_fake_and_network_free": True,
            "exact_fixture_cleanup_has_zero_residue": True,
        }
    elif recipe.scenario_id in APPLICATION_REGRESSION_LIVE_HANDLER_IDS:
        observed, independent = _run_application_regression(
            args,
            plan,
            scenario_id=recipe.scenario_id,
            observer=True,
        )
        contract = dict(observed)
    elif recipe.scenario_id == _SESSION_FAILOVER_ID:
        observed, independent = _session_failover_oracle(args, plan)
        contract = {
            "two_private_session_fixtures_never_enter_public_evidence": True,
            "fi_to_ir_transition_uses_schedule_bound_witness_receipt": True,
            "wa_ir_mutations_use_encrypted_object_storage_pull_only": True,
            "doer_and_oracle_session_continuity_are_separately_proved": True,
            "final_failback_and_private_fixture_cleanup_are_oracle_owned": True,
        }
    elif recipe.scenario_id in RECOVERY_TIMING_LIVE_HANDLER_IDS:
        observed, independent = _recovery_timing_verify(args, plan, runner)
        contract = {
            "two_closed_delivery_flaps_are_object_storage_pull_bound": True,
            "wa_ir_remains_the_only_witness_writer_and_public_origin": True,
            "durable_backlog_and_live_drain_overlap_are_journal_proved": True,
            "failure_recovery_resumes_then_cleans_exact_private_fixtures": True,
            "doer_and_oracle_execute_separate_recovery_cycles": True,
        }
    elif recipe.scenario_id in ONE_HOUR_BACKLOG_LIVE_HANDLER_IDS:
        observed, independent = _one_hour_backlog_verify(args, plan, runner)
        contract = {
            "sixty_minute_paused_delivery_window_is_durable_and_recoverable": True,
            "twenty_bounded_ir_writer_batches_exceed_one_rps_in_aggregate": True,
            "all_batch_backlog_receipts_and_final_live_routes_are_journal_proved": True,
            "wa_ir_pull_only_control_and_exact_fixture_cleanup": True,
            "doer_and_oracle_execute_separate_one_hour_cycles": True,
        }
    elif recipe.scenario_id in ENDURANCE_LIVE_HANDLER_IDS:
        observed, independent = _endurance_verify(args, plan, runner)
        contract = {
            "twenty_four_hour_monotonic_window_has_288_durable_samples": True,
            "bounded_real_fi_writer_ingress_converges_every_five_minutes": True,
            "writer_epoch_and_all_managed_fault_counts_remain_safe": True,
            "four_host_storage_and_database_growth_are_hard_bounded": True,
            "oracle_reopens_journal_then_makes_fresh_post_window_observation": True,
        }
    elif recipe.scenario_id in DESTRUCTIVE_WITNESS_HANDLER_IDS:
        observed, independent = _verify_witness_pause(args, plan, runner)
        contract = {
            "only_campaign_bound_reversible_witness_power_actions_are_used": True,
            "both_webapp_sites_fence_after_witness_vm_pause": True,
            "no_ir_promotion_occurs_without_national_cutoff": True,
            "witness_power_on_and_exact_fi_writer_recovery_are_proved": True,
            "oracle_performs_a_fresh_provider_writer_and_ingress_observation": True,
        }
    elif recipe.scenario_id in DESTRUCTIVE_FI_HOST_LOSS_HANDLER_IDS:
        observed, independent = _verify_fi_host_loss(args, plan, runner)
        contract = {
            "only_campaign_bound_reversible_fi_power_actions_are_used": True,
            "wa_ir_is_observed_only_through_its_pull_agent_while_fi_is_down": True,
            "no_ir_promotion_occurs_without_national_cutoff": True,
            "fi_power_on_and_exact_writer_recovery_are_proved": True,
            "oracle_performs_a_fresh_provider_writer_and_ingress_observation": True,
        }
    elif recipe.scenario_id in DESTRUCTIVE_IR_ACTIVE_ORIGIN_LOSS_HANDLER_IDS:
        observed, independent = _verify_ir_active_origin_loss(args, plan, runner)
        contract = {
            "only_campaign_bound_reversible_ir_power_actions_are_used": True,
            "wa_fi_remains_fenced_while_ir_is_the_recorded_writer": True,
            "public_ingress_fails_closed_without_substitute_origin": True,
            "ir_power_on_restores_the_exact_active_epoch": True,
            "oracle_performs_a_fresh_provider_writer_and_ingress_observation": True,
        }
    elif recipe.scenario_id in DESTRUCTIVE_FI_RECOVERY_HUB_LOSS_HANDLER_IDS:
        observed, independent = _verify_fi_recovery_hub_loss(args, plan, runner)
        contract = {
            "only_campaign_bound_reversible_fi_power_actions_are_used": True,
            "wa_ir_is_observed_only_through_its_pull_agent_while_fi_is_down": True,
            "fi_hub_loss_never_changes_the_ir_writer_or_public_origin": True,
            "fi_hub_rejoin_reconverges_without_new_direct_bot_to_ir_path": True,
            "oracle_performs_a_fresh_provider_writer_and_ingress_observation": True,
        }
    elif recipe.scenario_id == "power_loss_between_fence_and_enable":
        observed, independent = _verify_power_loss_cutpoint(args, plan, runner)
        contract = {
            "durable_source_fence_and_connection_drain_precede_fi_power_loss": True,
            "same_schedule_bound_jit_plan_resumes_after_source_power_loss": True,
            "wa_ir_target_enable_uses_encrypted_object_storage_pull_only": True,
            "exact_next_ir_writer_epoch_and_ingress_are_proved": True,
            "fi_rejoins_as_standby_before_following_ir_active_scenarios": True,
        }
    elif recipe.scenario_id in DESTRUCTIVE_CAPACITY_HANDLER_IDS:
        observed, independent = _verify_capacity_fault(args, plan, runner)
        contract = {
            "dedicated_mount_capacity_is_reserved_without_volume_mutation": True,
            "writer_fence_rejects_unsafe_http_write_before_data_plane_error": True,
            "wal_event_redis_blob_planes_are_on_the_same_guarded_mount": True,
            "reserve_cleanup_restores_headroom_before_marker_removal": True,
            "oracle_rechecks_zero_capacity_fault_residue_and_writer_convergence": True,
        }
    elif recipe.scenario_id in TIMING_LIVE_HANDLER_IDS:
        observed, independent = _timing_live_verify(
            args,
            plan,
            recipe.scenario_id,
            runner,
        )
        contract = {
            "bounded_real_business_event_ingress": True,
            "three_independent_ntp_clock_observations": True,
            "journal_correlated_per_hop_timing": True,
            "operation_bound_cleanup_state": True,
        }
    elif recipe.scenario_id in INGRESS_LIVE_HANDLER_IDS:
        observed, independent = _ingress_route_observation(plan)
        contract = _ingress_route_contract()
    elif recipe.scenario_id in ARTIFACT_ANCHOR_HANDLER_IDS:
        observed, independent = _artifact_anchor_observation(args, plan)
        contract = _artifact_anchor_contract()
    elif recipe.scenario_id in CDN_LIVE_HANDLER_IDS:
        observed, independent = _cdn_dynamic_cache_observation(plan)
        contract = _cdn_dynamic_cache_contract()
    elif recipe.scenario_id in CANONICAL_INGRESS_LIVE_HANDLER_IDS:
        observed, independent = _canonical_ingress_observation(plan)
        contract = _canonical_ingress_contract()
    elif recipe.scenario_id in REPEATABILITY_LIVE_HANDLER_IDS:
        observed, independent = _repeatability_observation(
            args,
            plan,
            allow_write=False,
        )
        contract = _repeatability_contract()
    elif recipe.scenario_id in FINAL_WRITER_HANDLER_IDS:
        observed, independent = _final_writer_route_observation(args, plan)
        contract = {
            "fresh_three_database_convergence_before_route_assertion": True,
            "webapp_fi_is_the_only_witness_leased_writer": True,
            "webapp_ir_is_fenced_standby": True,
            "writer_epoch_transition_and_lease_replicated_exactly": True,
            "all_managed_fault_residue_zero": True,
        }
    elif recipe.scenario_id == "iran_international_cutoff_promotes_ir":
        observed, independent = _verify_promoted_ir_lifecycle(args, plan, runner)
        contract = {
            "exact_scheduled_promotion_is_witness_receipt_bound": True,
            "fresh_isolated_connectivity_is_required_before_promotion": True,
            "wa_ir_mutations_use_encrypted_object_storage_pull_only": True,
            "writer_and_public_ingress_are_independently_reobserved": True,
            "ir_active_checkpoint_is_private_owner_only_and_iteration_bound": True,
        }
    elif recipe.scenario_id == "fi_epoch_reacquire_and_route_switch":
        observed, independent = _verify_failed_back_to_fi_lifecycle(args, plan, runner)
        contract = {
            "exact_scheduled_failback_is_witness_receipt_bound": True,
            "wa_ir_source_drain_and_fence_use_encrypted_object_storage_pull_only": True,
            "writer_and_public_ingress_are_independently_reobserved": True,
            "ir_active_checkpoint_is_removed_only_after_verified_fi_failback": True,
        }
    elif recipe.scenario_id in DR_POLICY_FIXTURE_IDS:
        observed, independent = _dr_policy_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=True,
        )
        contract = _dr_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in DR_FAULT_POLICY_FIXTURE_IDS:
        observed, independent = _dr_fault_policy_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=True,
        )
        contract = _dr_fault_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in FAILOVER_FAULT_POLICY_FIXTURE_IDS:
        observed, independent = _failover_fault_policy_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=True,
        )
        contract = _failover_fault_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in RECOVERY_POLICY_FIXTURE_IDS:
        observed, independent = _recovery_policy_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=True,
        )
        contract = _recovery_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in RUNTIME_POLICY_FIXTURE_IDS:
        observed, independent = _runtime_policy_fixture_observation(
            args,
            plan,
            recipe.scenario_id,
        )
        contract = _runtime_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in RELEASE_TRANSITION_POLICY_FIXTURE_IDS:
        observed, independent = _release_transition_policy_observation(
            plan,
            recipe.scenario_id,
            observer=True,
        )
        contract = _release_transition_policy_contract(recipe.scenario_id)
    elif recipe.scenario_id in QUEUE_POLICY_FIXTURE_IDS:
        observed, independent = _queue_policy_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=True,
        )
        contract = _queue_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id in SECURITY_POLICY_FIXTURE_IDS:
        observed, independent = _security_policy_fixture_observation(
            plan,
            recipe.scenario_id,
            observer=True,
        )
        contract = _security_policy_fixture_contract(recipe.scenario_id)
    elif recipe.scenario_id == "cross_service_secret_boundaries":
        observed, independent = _secret_boundary_observation(args, plan)
        contract = {
            "release_compose_secret_allowlist_verified": True,
            "three_independent_observer_invocations": True,
            "four_live_role_bundles_release_bound": True,
            "secret_values_never_emitted": True,
        }
    elif recipe.scenario_id in CONVERGENCE_HANDLER_IDS:
        independent = _convergence_states(plan)
        observed = _convergence_outcome(
            independent,
            scenario_id=recipe.scenario_id,
        )
        contract = _convergence_contract(recipe.scenario_id)
    elif recipe.scenario_id in {
        "production_host_domain_bucket_untouched",
        "production_boundaries_reverified",
    }:
        observed, independent = _production_boundary_observation(args, plan)
        contract = {
            "fresh_role_identities_checked": True,
            "production_hosts_excluded": True,
            "production_volumes_excluded": True,
            "production_buckets_excluded": True,
            "production_domains_not_targeted": True,
        }
    elif recipe.scenario_id in CLEANUP_LIVE_HANDLER_IDS:
        observed, independent = _cleanup_live_observation(
            args,
            plan,
            recipe.scenario_id,
        )
        contract = _cleanup_live_contract(recipe.scenario_id)
    else:
        raise LiveMatrixError("implemented oracle dispatch is incomplete")
    session_contract = recipe.scenario_id == _SESSION_FAILOVER_ID
    session_doer_expected = {
        "two_private_fi_session_fixtures_prepared": True,
        "schedule_bound_promotion_to_ir_completed": True,
        "doer_session_continuity_proved_on_ir": True,
    }
    if (
        retained_contract != contract
        or (not session_contract and retained_expected != observed)
        or (
            session_contract
            and (
                retained_expected != session_doer_expected
                or not all(value is True for value in observed.values())
            )
        )
        or recipe.scenario_id
        == "fresh_main_queue_dr_histories_equal"
        and observed["all_three_database_heads_equal"] is not True
        or recipe.scenario_id == "least_privilege_roles_attested"
        and observed["least_privilege_roles_attested"] is not True
        or recipe.scenario_id == "set_role_and_cross_role_access_denied"
        and observed["least_privilege_roles_attested"] is not True
        or recipe.scenario_id == "four_role_identity_isolated"
        and observed["four_role_identity_isolated"] is not True
    ):
        raise LiveMatrixError("independent live recipe observation differs")
    oracle_contract = (
        {
            **contract,
            "doer_and_oracle_session_proofs_are_complementary": True,
            "oracle_executed_final_failback_and_private_cleanup": True,
            "observer_transport_separate_invocation": True,
        }
        if session_contract
        else {
            **contract,
            "doer_and_observer_results_equal": True,
            "observer_transport_separate_invocation": True,
        }
    )
    customer_assertions: list[dict[str, Any]] = []
    if recipe.scenario_id in CUSTOMER_ACTOR_LIVE_HANDLER_IDS:
        customer_assertions = _retain_customer_actor_evidence(
            args,
            scenario_id=recipe.scenario_id,
            observations=independent,
        )
    sync_timing = None
    if recipe.scenario_id in (
        TIMING_LIVE_HANDLER_IDS
        | RECOVERY_TIMING_LIVE_HANDLER_IDS
        | ONE_HOUR_BACKLOG_LIVE_HANDLER_IDS
    ):
        timing_evidence = independent.get("timing_evidence")
        artifact = independent.get("artifact")
        verified_timing = (
            verify_sync_timing_evidence(artifact, scenario_id=recipe.scenario_id)
            if isinstance(artifact, dict)
            else None
        )
        if (
            not isinstance(timing_evidence, dict)
            or set(timing_evidence) != {"path", "sha256", "size"}
            or not isinstance(verified_timing, dict)
        ):
            raise LiveMatrixError("independent timing artifact retention is incomplete")
        sync_timing = {
            "policy": sync_timing_policy(recipe.scenario_id),
            "observed": {
                "policy_satisfied": True,
                "sample_count": verified_timing["sample_count"],
                "observed_requests_per_second": verified_timing[
                    "observed_requests_per_second"
                ],
            },
            "evidence": timing_evidence,
        }
    return {
        "expected_outcome": retained_expected,
        "observed_outcome": observed,
        "oracle_contract": oracle_contract,
        "oracle_observed": oracle_contract,
        "independent_observations": independent,
        "independent_observations_sha256": hash_summary(independent),
        "customer_assertions": customer_assertions,
        "sync_timing": sync_timing,
    }
