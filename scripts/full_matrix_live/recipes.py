"""Exhaustive source-owned recipe classification for the live Matrix.

Classification is deliberately separate from implementation.  A scenario is
executable only after its exact id is present in ``IMPLEMENTED_RECIPES`` and
both the named doer and oracle exist.  This prevents a generic command runner
or a broad phase test from silently standing in for an unimplemented case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from core.three_site_full_matrix_campaign import (
    PHASE_SCENARIOS,
    SCENARIO_EXECUTION_CLASS,
)


@dataclass(frozen=True, slots=True)
class Recipe:
    phase: str
    scenario_id: str
    execution_class: str
    doer: str
    oracle: str
    implemented: bool


PHASE_PROFILE: Final[dict[str, tuple[str, str]]] = {
    "migration_topology": ("migration_fixture", "migration_state"),
    "combined_workload": ("business_workload", "business_state"),
    "queue_faults": ("queue_fault", "queue_state"),
    "dr_faults": ("dr_fault", "dr_state"),
    "partitions_failover": ("failover_fault", "writer_route_state"),
    "recovery_failback": ("recovery_cycle", "writer_route_parity"),
    "security_isolation": ("security_attack", "security_boundary"),
    "capacity_dpi": ("capacity_workload", "capacity_timing"),
    "application_regression": ("application_workload", "application_state"),
    "cleanup_repeatability": ("cleanup_probe", "zero_residue"),
}

PROFILE_OVERRIDES: Final[dict[str, tuple[str, str]]] = {
    "backup_restore_rehearsed": ("backup_restore", "restored_semantic_parity"),
    "legacy_rollback_rehearsed": ("legacy_rollback", "legacy_source_identity"),
    "customer_actor_matrix_normal_fi_active": (
        "customer_actor_lifecycle",
        "customer_actor_normal_fi",
    ),
    "customer_actor_matrix_iran_active_outage": (
        "customer_actor_lifecycle",
        "customer_actor_iran_active",
    ),
    "customer_actor_matrix_recovery_ir_routed": (
        "customer_actor_lifecycle",
        "customer_actor_recovery_ir",
    ),
    "customer_actor_matrix_post_failback_fi_active": (
        "customer_actor_lifecycle",
        "customer_actor_post_failback_fi",
    ),
    "three_site_sync_timing_steady_state": (
        "timed_steady_workload",
        "sync_timing_observer",
    ),
    "three_hundred_rps_fifty_fifty": (
        "timed_300rps_workload",
        "sync_timing_observer",
    ),
    "reconnect_flap_and_bounded_catchup": (
        "timed_reconnect_workload",
        "sync_timing_observer",
    ),
    "one_hour_backlog_with_live_traffic": (
        "timed_backlog_workload",
        "sync_timing_observer",
    ),
    "twenty_four_hour_endurance_no_growth": (
        "endurance_24h_workload",
        "endurance_growth_oracle",
    ),
    "witness_partition_and_vm_pause": (
        "destructive_witness_pause",
        "writer_safety_oracle",
    ),
    "fi_host_loss_without_national_cutoff": (
        "destructive_fi_host_loss",
        "writer_safety_oracle",
    ),
    "permanent_fi_recovery_hub_loss": (
        "destructive_recovery_hub_loss",
        "safe_unavailable_oracle",
    ),
    "ir_only_active_origin_loss_is_safe_unavailable": (
        "destructive_ir_origin_loss",
        "safe_unavailable_oracle",
    ),
    "power_loss_between_fence_and_enable": (
        "destructive_power_cut",
        "writer_safety_oracle",
    ),
    "wal_event_redis_blob_capacity_exhaustion_safe": (
        "destructive_capacity_exhaustion",
        "safe_exhaustion_oracle",
    ),
}

# IDs are added only with their real doer, independent oracle, cleanup recipe,
# and focused source tests in the same reviewed change.
IMPLEMENTED_RECIPES: Final[frozenset[str]] = frozenset(
    {
        "acknowledged_source_event_absent_target_blocks_promotion",
        "backup_counts_pass_semantic_parity_fails",
        "blob_database_asymmetric_failure_resume",
        "bot_and_webapp_offers_concurrent",
        "business_event_delivery_commit_boundaries",
        "counter_double_increment_fixture",
        "claim_limiter_provider_crash_boundaries",
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
        "queue_publication_edit_callback_private",
        "provider_success_outcome_ambiguity",
        "production_host_domain_bucket_untouched",
        "protocol_schema_key_rotation_mismatch",
        "rate_limit_timeout_malformed_response",
        "receive_ack_apply_checkpoint_boundaries",
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
        "market_trade_account_admin_regression",
        "missing_or_corrupt_blob_blocks_readiness",
        "unique_ids_real_business_conflict_quarantined",
        "applied_checkpoint_conflict_effect_gates",
        "database_and_blob_final_parity",
        "queue_jobs_effects_conflicts_reconciled",
        "requests_trades_partial_settlement",
        "fake_event_and_raw_sql_bypass_denied",
        "startup_mutation_on_fenced_standby_rejected",
        "production_boundaries_reverified",
        "legacy_staging_clone_migrated",
        "messenger_upload_download_regression",
        "cross_service_secret_boundaries",
        "wrong_pairwise_identity_and_nonce_replay",
        "restored_old_epoch_effects_remain_fenced",
        "runtime_cutover_and_forward_rollback",
        "expired_plan_only_safe_fenced_recovery",
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
        "bot_remains_active_all_outage_classes",
        "database_blob_inverse_completion_reconciles",
        "fi_epoch_reacquire_and_route_switch",
        "file_transfer_interruption_resumes_by_hash",
        "final_write_barrier_with_live_arrivals",
        "ir_remains_active_during_recovery",
        "old_http_websocket_connections_drained",
        "recovery_and_failback_restart_resume",
        "short_medium_long_outage_rules",
        "ambiguous_client_command_retry_is_idempotent",
        "batch_flush_inflight_boundaries",
        "database_redis_blob_storage_watermarks",
        "dpi_request_byte_budget_enforced",
        "dropped_wakeup_still_durably_drains",
        "finland_directions_one_fifty_events_each",
        "healthy_link_never_accumulates_backlog",
        "recovery_eta_and_non_starvation",
        "reconnect_flap_and_bounded_catchup",
        "relay_preserves_origin_without_echo",
        "webapp_dr_three_hundred_events_amplified",
        "websocket_reconnect_and_cursor_reconcile",
        "writer_renewal_and_dr_relay_under_load",
        "fi_host_loss_without_national_cutoff",
        "ir_only_active_origin_loss_is_safe_unavailable",
        "permanent_fi_recovery_hub_loss",
        "power_loss_between_fence_and_enable",
        "wal_event_redis_blob_capacity_exhaustion_safe",
        "witness_partition_and_vm_pause",
        "three_site_sync_timing_steady_state",
        "three_hundred_rps_fifty_fifty",
        "test_ingress_same_release_and_data_plane",
        "artifact_hash_chain_and_external_anchor",
        "cdn_dynamic_cache_and_stale_health_denied",
        "canonical_staging_domain_auth_cors_links",
        "customer_actor_matrix_normal_fi_active",
        "customer_actor_matrix_iran_active_outage",
        "customer_actor_matrix_recovery_ir_routed",
        "customer_actor_matrix_post_failback_fi_active",
        "second_cycle_same_or_stronger_oracles",
        "writer_epoch_route_and_standby_final_state",
        "session_failover_contract",
    }
)


def _build() -> dict[str, Recipe]:
    result: dict[str, Recipe] = {}
    for phase, scenario_ids in PHASE_SCENARIOS.items():
        default = PHASE_PROFILE[phase]
        for scenario_id in scenario_ids:
            doer, oracle = PROFILE_OVERRIDES.get(scenario_id, default)
            result[scenario_id] = Recipe(
                phase=phase,
                scenario_id=scenario_id,
                execution_class=SCENARIO_EXECUTION_CLASS[scenario_id],
                doer=doer,
                oracle=oracle,
                implemented=scenario_id in IMPLEMENTED_RECIPES,
            )
    return result


RECIPES: Final[dict[str, Recipe]] = _build()

_EXPECTED = {
    scenario_id
    for scenario_ids in PHASE_SCENARIOS.values()
    for scenario_id in scenario_ids
}
if (
    set(RECIPES) != _EXPECTED
    or not set(IMPLEMENTED_RECIPES).issubset(_EXPECTED)
    or set(PHASE_PROFILE) != set(PHASE_SCENARIOS)
):
    raise RuntimeError("live Full Matrix recipe classification is incomplete")


def recipe_for(phase: str, scenario_id: str) -> Recipe:
    recipe = RECIPES.get(scenario_id)
    if recipe is None or recipe.phase != phase:
        raise KeyError("live Full Matrix scenario recipe identity is invalid")
    return recipe


def missing_recipes() -> tuple[str, ...]:
    return tuple(
        scenario_id
        for scenario_id, recipe in RECIPES.items()
        if not recipe.implemented
    )
