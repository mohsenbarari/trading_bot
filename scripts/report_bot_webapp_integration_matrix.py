#!/usr/bin/env python3
"""Report the Bot/WebApp cross-server scenario matrix.

This matrix is the Step 11 gate for the Bot/WebApp integration contract. It is
intentionally evidence-oriented: every automated claim points at an existing
test file and a named test snippet, while staging-only checks remain explicit
manual evidence requirements.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_VERSION = "step-11-2026-06-19"

REQUIRED_LAYERS = (
    "unit_policy",
    "service_command",
    "webapp_api_sync_receive",
    "bot_handler",
    "telegram_channel_state",
    "sync_worker",
    "integration_e2e_practical",
    "staging_manual",
)


@dataclass(frozen=True)
class CoverageRef:
    layer: str
    path: str
    snippet: str


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    risk_area: str
    coverage_refs: tuple[CoverageRef, ...]
    staging_checks: tuple[str, ...]


def ref(layer: str, path: str, snippet: str) -> CoverageRef:
    return CoverageRef(layer=layer, path=path, snippet=snippet)


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        scenario_id="S11-01",
        title="Bot-created offer stays foreign-home and becomes visible on Iran WebApp",
        risk_area="offer_creation_sync_visibility",
        coverage_refs=(
            ref(
                "bot_handler",
                "tests/test_bot_trade_create_confirm_success_wholesale.py",
                "test_handle_trade_confirm_publishes_wholesale_offer_and_updates_channel_message",
            ),
            ref(
                "unit_policy",
                "tests/test_offer_source_surface.py",
                "test_offer_home_server_is_decided_by_source_surface",
            ),
            ref(
                "webapp_api_sync_receive",
                "tests/test_sync_router_receive_basic.py",
                "test_receive_sync_data_publishes_created_realtime_for_synced_iran_offer",
            ),
            ref(
                "webapp_api_sync_receive",
                "tests/test_offers_public_routes.py",
                "test_public_route_returns_safe_fields_for_synced_iran_and_foreign_offers",
            ),
        ),
        staging_checks=(
            "Create a synthetic offer from the foreign Telegram bot and confirm Iran WebApp list/detail visibility within the accepted lag window.",
            "Confirm the offer row keeps home_server=foreign on both databases.",
        ),
    ),
    Scenario(
        scenario_id="S11-02",
        title="WebApp-created offer stays Iran-home and is published to foreign Telegram",
        risk_area="offer_creation_sync_visibility",
        coverage_refs=(
            ref(
                "webapp_api_sync_receive",
                "tests/test_offers_router_create_success.py",
                "test_create_offer_uses_webapp_home_server_even_when_owner_and_runtime_are_foreign",
            ),
            ref(
                "webapp_api_sync_receive",
                "tests/test_sync_router_receive_offer_publish.py",
                "test_receive_sync_data_publishes_new_foreign_offer",
            ),
            ref(
                "service_command",
                "tests/test_telegram_offer_publication_service.py",
                "test_publish_success_records_telegram_result_for_sync_back",
            ),
        ),
        staging_checks=(
            "Create a synthetic offer from Iran WebApp and confirm one foreign Telegram channel post is created.",
            "Confirm the offer row keeps home_server=iran on both databases and Telegram state syncs back.",
        ),
    ),
    Scenario(
        scenario_id="S11-03",
        title="Stable offer public identity resolves links and callbacks without cross-server integer IDs",
        risk_area="public_identity_links",
        coverage_refs=(
            ref("unit_policy", "tests/test_sync_metadata.py", "test_offer_metadata_uses_public_identity_authority_and_version"),
            ref("unit_policy", "tests/test_sync_metadata.py", "test_public_identity_payloads_prefer_stable_cross_server_keys"),
            ref(
                "webapp_api_sync_receive",
                "tests/test_offers_public_routes.py",
                "test_public_route_returns_safe_fields_for_synced_iran_and_foreign_offers",
            ),
            ref(
                "bot_handler",
                "tests/test_bot_trade_execute_local_success.py",
                "test_public_channel_trade_callback_resolves_offer_by_public_identity",
            ),
        ),
        staging_checks=(
            "Open the public offer link from Telegram and from WebApp history; confirm it resolves on Iran WebApp.",
            "Trigger a Telegram callback from the public identity and confirm it targets the expected offer.",
        ),
    ),
    Scenario(
        scenario_id="S11-04",
        title="Owner expiry from WebApp mutates only on the authoritative home server",
        risk_area="expiry_authority",
        coverage_refs=(
            ref("service_command", "tests/test_offer_expiry_service.py", "test_authoritative_expiry_records_owner_metadata_and_commits"),
            ref("webapp_api_sync_receive", "tests/test_offers_router_expire.py", "test_expire_offer_updates_status_and_publishes_side_effects"),
            ref("webapp_api_sync_receive", "tests/test_offers_router_expire.py", "test_expire_offer_forwards_remote_home_without_local_mutation"),
            ref("webapp_api_sync_receive", "tests/test_offers_router_expire.py", "test_expire_offer_remote_home_outage_does_not_mutate_locally"),
        ),
        staging_checks=(
            "Expire an Iran-home offer from WebApp and confirm the foreign Telegram post is terminal and buttonless after sync.",
            "Expire a foreign-home offer from WebApp and confirm the WebApp forwards instead of mutating locally.",
        ),
    ),
    Scenario(
        scenario_id="S11-05",
        title="Owner expiry from Telegram bot records bot source and removes Telegram interactions",
        risk_area="expiry_authority",
        coverage_refs=(
            ref("bot_handler", "tests/test_bot_trade_manage_success.py", "test_handle_expire_offer_expires_offer_and_removes_buttons"),
            ref("telegram_channel_state", "tests/test_telegram_offer_channel_service.py", "test_apply_pure_expired_edits_text_and_removes_buttons"),
            ref("webapp_api_sync_receive", "tests/test_offers_router_expire.py", "test_internal_expire_records_forwarded_source_metadata"),
        ),
        staging_checks=(
            "Expire a foreign-home offer from the bot and confirm the channel text receives the expired marker and inline buttons are gone.",
            "Confirm Iran WebApp receives the expired state without attempting any Telegram call.",
        ),
    ),
    Scenario(
        scenario_id="S11-06",
        title="User-triggered expiry records source platform, server, owner, and actor metadata",
        risk_area="expiry_audit_metadata",
        coverage_refs=(
            ref("service_command", "tests/test_offer_expiry_service.py", "test_authoritative_expiry_records_owner_metadata_and_commits"),
            ref("webapp_api_sync_receive", "tests/test_offers_router_expire.py", "test_internal_expire_records_forwarded_source_metadata"),
            ref("bot_handler", "tests/test_bot_trade_manage_success.py", "test_handle_expire_offer_expires_offer_and_removes_buttons"),
        ),
        staging_checks=(
            "Compare WebApp and bot manual expiry rows and verify expire_source_surface, expire_source_server, expired_by_user_id, and expired_by_actor_user_id.",
        ),
    ),
    Scenario(
        scenario_id="S11-07",
        title="Automatic expiry records lifetime/system metadata without fabricating a user actor",
        risk_area="expiry_audit_metadata",
        coverage_refs=(
            ref("service_command", "tests/test_offer_expiry.py", "test_expire_stale_offers_expires_offers_and_runs_side_effects"),
            ref("service_command", "tests/test_offer_expiry_service.py", "test_system_recovery_finalization_has_no_false_user_actor"),
            ref("unit_policy", "tests/test_offer_request_policy.py", "test_legacy_expire_reason_mapping_does_not_fabricate_missing_metadata"),
        ),
        staging_checks=(
            "Let a synthetic active offer pass its lifetime and verify expire_reason=time_limit, source_surface=system, and no user actor.",
        ),
    ),
    Scenario(
        scenario_id="S11-08",
        title="Telegram terminal markers distinguish fully traded, partially traded, and expired offers",
        risk_area="telegram_projection",
        coverage_refs=(
            ref("telegram_channel_state", "tests/test_telegram_offer_channel_service.py", "test_history_tag_contract"),
            ref("telegram_channel_state", "tests/test_telegram_offer_channel_service.py", "test_apply_terminal_completed_edits_text_and_removes_buttons_on_foreign"),
            ref("telegram_channel_state", "tests/test_telegram_offer_channel_service.py", "test_apply_partially_traded_expired_edits_text_and_removes_buttons"),
            ref("telegram_channel_state", "tests/test_telegram_offer_channel_service.py", "test_apply_pure_expired_edits_text_and_removes_buttons"),
        ),
        staging_checks=(
            "Inspect one full trade post for the line containing the handshake/check markers.",
            "Inspect one partial trade post for the traded quantity between the handshake/check markers.",
            "Inspect one expired post for the expired marker line.",
        ),
    ),
    Scenario(
        scenario_id="S11-09",
        title="Telegram terminal edits remove inline buttons and tolerate duplicate/not-modified edits",
        risk_area="telegram_projection",
        coverage_refs=(
            ref("telegram_channel_state", "tests/test_telegram_offer_channel_service.py", "test_apply_terminal_completed_edits_text_and_removes_buttons_on_foreign"),
            ref("telegram_channel_state", "tests/test_telegram_offer_channel_service.py", "test_apply_pure_expired_edits_text_and_removes_buttons"),
            ref("telegram_channel_state", "tests/test_telegram_offer_channel_service.py", "test_message_not_modified_is_idempotent_success"),
            ref("bot_handler", "tests/test_bot_trade_execute_update_markup.py", "test_update_offer_channel_markup_handles_missing_completed_and_active_offers"),
        ),
        staging_checks=(
            "After trade, manual expiry, auto-expiry, and duplicate replay, confirm channel posts have no actionable buttons.",
        ),
    ),
    Scenario(
        scenario_id="S11-10",
        title="WebApp and bot request/trade paths work against Iran-home and foreign-home offers",
        risk_area="trade_authority_forwarding",
        coverage_refs=(
            ref("service_command", "tests/test_trades_router_authoritative_success.py", "test_execute_trade_authoritatively_persists_trade_and_runs_side_effects"),
            ref("webapp_api_sync_receive", "tests/test_trades_router_execution_wrappers.py", "test_create_trade_returns_forwarded_response_when_remote_home"),
            ref("webapp_api_sync_receive", "tests/test_trades_router_execution_wrappers.py", "test_forward_trade_if_remote_home_covers_both_cross_server_directions_and_idempotency"),
            ref("bot_handler", "tests/test_bot_trade_execute_local_success.py", "test_handle_channel_trade_delegates_confirmed_local_trade_to_shared_command"),
            ref("bot_handler", "tests/test_bot_trade_execute_remote_home.py", "test_handle_channel_trade_remote_home_handles_pending_suggestion_success_and_error"),
        ),
        staging_checks=(
            "Execute WebApp and bot trade requests against one Iran-home offer and one foreign-home offer.",
            "Confirm the authoritative server owns mutation and the peer receives the final state.",
        ),
    ),
    Scenario(
        scenario_id="S11-11",
        title="Request ledger captures success, rejection, lot unavailable, stale/conflict, replay, and after-expiry outcomes",
        risk_area="request_ledger",
        coverage_refs=(
            ref("service_command", "tests/test_offer_request_ledger_service.py", "test_customer_snapshot_and_rejected_request_are_durable_without_trade"),
            ref("service_command", "tests/test_offer_request_ledger_service.py", "test_duplicate_idempotency_replay_returns_existing_row_without_insert"),
            ref("service_command", "tests/test_trades_router_authoritative_success.py", "test_execute_trade_authoritatively_converts_stale_commit_to_conflict"),
            ref("service_command", "tests/test_trades_router_authoritative_guards.py", "test_execute_trade_authoritatively_rejects_invalid_amount_and_reuses_idempotent_trade"),
            ref("service_command", "tests/test_trades_router_authoritative_guards.py", "test_execute_trade_authoritatively_rejects_duplicate_failed_request_without_mutation"),
        ),
        staging_checks=(
            "Create accepted, rejected, duplicate, stale/conflict, lot-unavailable, and after-expiry synthetic requests; review ledger rows.",
        ),
    ),
    Scenario(
        scenario_id="S11-12",
        title="Request ledger state machine is append-safe and terminal outcomes are not contradicted",
        risk_area="request_ledger",
        coverage_refs=(
            ref("unit_policy", "tests/test_offer_request_ledger_model.py", "test_model_has_required_columns_indexes_and_status_values"),
            ref("service_command", "tests/test_offer_request_ledger_service.py", "test_terminal_rows_cannot_change_to_contradictory_outcome"),
            ref("service_command", "tests/test_offer_request_ledger_service.py", "test_webapp_and_bot_requests_record_source_metadata"),
        ),
        staging_checks=(
            "Review ledger rows for received, authorized/completed, rejected-terminal, duplicate-replay, and failed-internal transitions.",
        ),
    ),
    Scenario(
        scenario_id="S11-13",
        title="Public failure visibility is separated from internal failure context",
        risk_area="privacy_audit_visibility",
        coverage_refs=(
            ref("unit_policy", "tests/test_offer_request_policy.py", "test_public_link_payload_does_not_expose_sensitive_request_metadata"),
            ref("unit_policy", "tests/test_offer_request_policy.py", "test_owner_and_admin_audit_visibility_are_explicitly_gated"),
            ref("webapp_api_sync_receive", "tests/test_offers_public_routes.py", "test_admin_detail_includes_publication_state_and_admin_failure_context"),
        ),
        staging_checks=(
            "Compare unauthenticated, owner, and admin views for the same failed request and confirm only authorized views expose internal context.",
        ),
    ),
    Scenario(
        scenario_id="S11-14",
        title="Customer requester ledger rows snapshot relation metadata and restrict visibility",
        risk_area="privacy_audit_visibility",
        coverage_refs=(
            ref("service_command", "tests/test_offer_request_ledger_service.py", "test_customer_snapshot_and_rejected_request_are_durable_without_trade"),
            ref("unit_policy", "tests/test_offer_request_policy.py", "test_owner_and_admin_audit_visibility_are_explicitly_gated"),
            ref("service_command", "tests/test_trades_router_authoritative_success.py", "test_execute_trade_authoritatively_creates_two_legs_for_tier2_customer_on_outsider_owner_offer"),
        ),
        staging_checks=(
            "Submit a customer request and verify relation snapshots remain stable after relation edits.",
            "Confirm non-authorized viewers cannot read customer relation metadata.",
        ),
    ),
    Scenario(
        scenario_id="S11-15",
        title="Offer detail link shows safe public fields and authorized metadata by viewer",
        risk_area="public_identity_links",
        coverage_refs=(
            ref("webapp_api_sync_receive", "tests/test_offers_public_routes.py", "test_public_detail_denies_unauthenticated_and_unrelated_viewers"),
            ref("webapp_api_sync_receive", "tests/test_offers_public_routes.py", "test_owner_detail_returns_bounded_sanitized_ledger_without_admin_publication_state"),
            ref("webapp_api_sync_receive", "tests/test_offers_public_routes.py", "test_admin_detail_includes_publication_state_and_admin_failure_context"),
        ),
        staging_checks=(
            "Open the same offer detail link as public, unrelated user, owner, and admin; compare allowed metadata.",
        ),
    ),
    Scenario(
        scenario_id="S11-16",
        title="Offer detail ledger pagination, retention, and archive assumptions remain bounded",
        risk_area="request_ledger",
        coverage_refs=(
            ref("service_command", "tests/test_offer_request_ledger_service.py", "test_history_query_is_paginated_and_ordered"),
            ref("webapp_api_sync_receive", "tests/test_offers_public_routes.py", "test_owner_detail_returns_bounded_sanitized_ledger_without_admin_publication_state"),
        ),
        staging_checks=(
            "Seed enough requests to cross the first ledger page and confirm bounded pagination on offer detail.",
            "Record current archive/retention assumptions in the staging evidence note.",
        ),
    ),
    Scenario(
        scenario_id="S11-17",
        title="The same user can be active on WebApp and bot without changing offer source ownership",
        risk_area="cross_surface_identity",
        coverage_refs=(
            ref("integration_e2e_practical", "tests/test_offer_limit_cross_surface_smoke.py", "test_web_fifteenth_offer_blocks_bot_sixteenth_with_live_limit_message"),
            ref("integration_e2e_practical", "tests/test_offer_limit_cross_surface_smoke.py", "test_bot_fifteenth_offer_blocks_web_sixteenth_with_live_limit_message"),
            ref("unit_policy", "tests/test_offer_source_surface.py", "test_offer_home_server_is_decided_by_source_surface"),
            ref("unit_policy", "tests/test_session_authority.py", "test_assert_login_allowed_fails_closed_when_home_server_unavailable"),
        ),
        staging_checks=(
            "Use one synthetic user on both surfaces; create one bot offer and one WebApp offer and confirm their home_server values differ by source.",
            "Confirm session authority checks do not force the user into one surface only.",
        ),
    ),
    Scenario(
        scenario_id="S11-18",
        title="Near-simultaneous trade, expiry, and sync updates do not reactivate stale state",
        risk_area="concurrency_conflict_resolution",
        coverage_refs=(
            ref("unit_policy", "tests/test_sync_router_stale_events.py", "test_offer_upsert_uses_atomic_ordering_where_clause"),
            ref("unit_policy", "tests/test_sync_router_stale_events.py", "test_out_of_order_offer_update_after_expiry_does_not_reactivate"),
            ref("service_command", "tests/test_trade_atomicity_hardening.py", "test_commit_rolls_back_and_maps_stale_or_unique_conflicts"),
            ref("service_command", "tests/test_trades_router_authoritative_success.py", "test_execute_trade_authoritatively_converts_stale_commit_to_conflict"),
        ),
        staging_checks=(
            "Run a manual race probe with trade and expiry against the same synthetic offer and confirm one terminal state wins cleanly.",
        ),
    ),
    Scenario(
        scenario_id="S11-19",
        title="Duplicate callback, duplicate sync delivery, worker replay, and direct push failure are idempotent/retryable",
        risk_area="idempotency_replay",
        coverage_refs=(
            ref("unit_policy", "tests/test_sync_router_stale_events.py", "test_duplicate_terminal_offer_replay_is_idempotent"),
            ref("sync_worker", "tests/test_sync_worker.py", "test_main_requeues_non_200_response"),
            ref("sync_worker", "tests/test_sync_worker.py", "test_main_requeues_request_errors"),
            ref("sync_worker", "tests/test_sync_worker.py", "test_main_drains_committed_change_log_when_redis_has_no_wakeup"),
            ref("bot_handler", "tests/test_bot_trade_execute_local_success.py", "test_public_channel_trade_callback_resolves_offer_by_public_identity"),
        ),
        staging_checks=(
            "Replay the same Telegram callback/sync item and confirm no duplicate trade or contradictory offer state is created.",
            "Force a direct push failure in staging and confirm the committed outbox item retries.",
        ),
    ),
    Scenario(
        scenario_id="S11-20",
        title="Telegram publish failure is recorded, retried, and does not corrupt business offer state",
        risk_area="telegram_projection",
        coverage_refs=(
            ref("service_command", "tests/test_telegram_offer_publication_service.py", "test_publish_failure_is_recorded_as_retryable_failed_state"),
            ref("service_command", "tests/test_offer_publication_reconciliation_service.py", "test_foreign_repair_retries_failed_telegram_publication"),
            ref("service_command", "tests/test_offer_publication_state_service.py", "test_business_offer_state_is_separate_from_failed_telegram_publication"),
            ref("bot_handler", "tests/test_bot_trade_create_confirm_telegram_error.py", "test_handle_trade_confirm_rolls_back_offer_on_telegram_bad_request"),
        ),
        staging_checks=(
            "Simulate Telegram send failure and confirm publication_state records retryable failure while offer business state remains safe.",
            "Run the reconciliation repair path and confirm a single Telegram post is produced.",
        ),
    ),
    Scenario(
        scenario_id="S11-21",
        title="WebApp realtime failure is tolerated and recovered through sync/publication state",
        risk_area="webapp_realtime_recovery",
        coverage_refs=(
            ref("webapp_api_sync_receive", "tests/test_offers_router_create_success.py", "test_create_offer_tolerates_post_commit_cache_and_realtime_failures"),
            ref("service_command", "tests/test_offer_expiry.py", "test_expire_stale_offers_tolerates_realtime_and_cache_failures"),
            ref("webapp_api_sync_receive", "tests/test_sync_router_receive_basic.py", "test_receive_sync_data_publishes_local_realtime_for_synced_terminal_offers"),
            ref("service_command", "tests/test_offer_publication_state_service.py", "test_pending_publication_becomes_lagged_after_threshold"),
        ),
        staging_checks=(
            "Temporarily break WebApp realtime in staging, create/update an offer, then restore and confirm list/detail converge by API reload and sync state.",
        ),
    ),
    Scenario(
        scenario_id="S11-22",
        title="Unknown table, forbidden table, unsupported version, and sensitive fields fail closed",
        risk_area="sync_security_policy",
        coverage_refs=(
            ref("webapp_api_sync_receive", "tests/test_sync_router_fail_closed_policy.py", "test_unsupported_protocol_version_returns_partial_failure_without_apply"),
            ref("webapp_api_sync_receive", "tests/test_sync_router_fail_closed_policy.py", "test_unknown_table_returns_partial_failure_without_apply"),
            ref("webapp_api_sync_receive", "tests/test_sync_router_fail_closed_policy.py", "test_policy_forbidden_messenger_table_returns_partial_failure"),
            ref("unit_policy", "tests/test_sync_field_policy.py", "test_user_sensitive_and_no_sync_reference_fields_are_sanitized"),
            ref("sync_worker", "tests/test_sync_worker.py", "test_change_log_entry_to_sync_item_sanitizes_legacy_sensitive_user_payload"),
        ),
        staging_checks=(
            "Send one invalid sync batch in staging and confirm it is rejected/partial without marking unsafe rows as synced.",
        ),
    ),
    Scenario(
        scenario_id="S11-23",
        title="Short outage replay preserves committed payload identity and rejects stale terminal reversals",
        risk_area="outage_recovery",
        coverage_refs=(
            ref("sync_worker", "tests/test_sync_worker.py", "test_offer_change_log_replay_uses_original_committed_payload"),
            ref("sync_worker", "tests/test_sync_worker.py", "test_offer_change_log_replay_does_not_rebuild_same_sequence_from_current_state"),
            ref("sync_worker", "tests/test_sync_worker.py", "test_main_treats_outbound_payload_as_wakeup_and_sends_committed_change_log"),
            ref("unit_policy", "tests/test_sync_router_stale_events.py", "test_out_of_order_offer_update_after_expiry_does_not_reactivate"),
        ),
        staging_checks=(
            "Interrupt peer delivery for less than two minutes, trade/expire a synthetic offer, restore delivery, and confirm only the final state appears.",
        ),
    ),
    Scenario(
        scenario_id="S11-24",
        title="Medium and long outage recovery gates active publication until full catch-up and expires local-only active offers",
        risk_area="outage_recovery",
        coverage_refs=(
            ref("service_command", "tests/test_cross_server_recovery_service.py", "test_dirty_recovery_health_gates_without_mutation"),
            ref("service_command", "tests/test_cross_server_recovery_service.py", "test_clean_recovery_expires_candidates_and_creates_owner_notifications"),
            ref("service_command", "tests/test_cross_server_recovery_service.py", "test_clean_recovery_with_no_candidates_clears_publication_gate"),
            ref("webapp_api_sync_receive", "tests/test_sync_router_receive_offer_publish.py", "test_receive_sync_data_skips_foreign_active_publish_when_recovery_gate_enabled"),
            ref("webapp_api_sync_receive", "tests/test_sync_router_receive_offer_publish.py", "test_receive_sync_data_skips_iran_realtime_created_publish_when_recovery_gate_enabled"),
        ),
        staging_checks=(
            "Run medium-outage and long-outage rehearsal on synthetic data; confirm active-publication gate stays on until sync-health is clean.",
            "Confirm pre-recovery active local-only offers are expired, not published active to the peer.",
        ),
    ),
    Scenario(
        scenario_id="S11-25",
        title="Migration/cutover rehearsal requires zero active offers and fresh shared-sync state",
        risk_area="cutover_readiness",
        coverage_refs=(
            ref("unit_policy", "tests/test_shared_sync_state_inspector.py", "test_empty_signal_tables_are_fresh"),
            ref("integration_e2e_practical", "tests/test_deployment_restart_benchmark.py", "test_sync_health_clean_requires_empty_backlog_and_queues"),
            ref("integration_e2e_practical", "tests/test_deploy_surface_smoke.py", "test_staging_frontend_dist_isolated_from_production_artifact"),
        ),
        staging_checks=(
            "Before any cutover rehearsal, verify zero active offers on both staging servers.",
            "Run shared-state inspection and sync-health on both staging peers; abort if any backlog or partial publication remains.",
        ),
    ),
    Scenario(
        scenario_id="S11-26",
        title="Rollback and fail-closed behavior preserve data without destructive cleanup",
        risk_area="rollback_fail_closed",
        coverage_refs=(
            ref("webapp_api_sync_receive", "tests/test_offers_router_expire.py", "test_expire_offer_remote_home_outage_does_not_mutate_locally"),
            ref("webapp_api_sync_receive", "tests/test_trades_router_execution_wrappers.py", "test_create_trade_returns_remote_failure_without_local_partial_execution"),
            ref("webapp_api_sync_receive", "tests/test_sync_router_fail_closed_policy.py", "test_unknown_table_returns_partial_failure_without_apply"),
            ref("integration_e2e_practical", "tests/test_production_recoverability_tools.py", "test_evaluate_backup_requires_all_artifacts_and_restore_when_requested"),
        ),
        staging_checks=(
            "Rehearse rollback/fail-closed decisions on staging only; confirm no destructive cleanup is needed to return to the known-safe state.",
        ),
    ),
    Scenario(
        scenario_id="S11-27",
        title="Staging validation evidence is owner-led and separated from production peers/data",
        risk_area="staging_gate",
        coverage_refs=(
            ref("integration_e2e_practical", "tests/test_deploy_surface_smoke.py", "test_staging_frontend_dist_isolated_from_production_artifact"),
            ref("integration_e2e_practical", "tests/test_deployment_restart_benchmark.py", "test_gates_require_backup_and_restart_recovery"),
            ref("unit_policy", "tests/test_telegram_gateway_policy.py", "test_gateway_hard_fails_on_iran_before_http_call"),
        ),
        staging_checks=(
            "Record branch SHA, staging deploy artifact, test command output, sync-health snapshots, DB state, Telegram post state, WebApp realtime state, and session-surface behavior.",
            "Confirm the validation uses staging peers and synthetic data only; production peers/data are a stop condition.",
            "Owner signs off after manual scenarios pass; without this sign-off Step 11 is not production-ready.",
        ),
    ),
)


def scenario_layers(scenario: Scenario) -> set[str]:
    layers = {coverage.layer for coverage in scenario.coverage_refs}
    if scenario.staging_checks:
        layers.add("staging_manual")
    return layers


def missing_coverage_refs(repo_root: Path = REPO_ROOT) -> list[str]:
    missing: list[str] = []
    for scenario in SCENARIOS:
        for coverage in scenario.coverage_refs:
            source = repo_root / coverage.path
            if not source.exists():
                missing.append(f"{scenario.scenario_id}: missing file {coverage.path}")
                continue
            text = source.read_text(encoding="utf-8")
            if coverage.snippet not in text:
                missing.append(f"{scenario.scenario_id}: missing snippet {coverage.snippet} in {coverage.path}")
    return missing


def evaluate_matrix(repo_root: Path = REPO_ROOT) -> dict[str, object]:
    scenario_ids = [scenario.scenario_id for scenario in SCENARIOS]
    duplicate_ids = sorted({scenario_id for scenario_id in scenario_ids if scenario_ids.count(scenario_id) > 1})
    invalid_layers = sorted(
        {
            coverage.layer
            for scenario in SCENARIOS
            for coverage in scenario.coverage_refs
            if coverage.layer not in REQUIRED_LAYERS
        }
    )
    scenario_failures = [
        f"{scenario.scenario_id}: missing automated coverage reference"
        for scenario in SCENARIOS
        if not scenario.coverage_refs
    ]
    scenario_failures.extend(
        f"{scenario.scenario_id}: missing staging manual check"
        for scenario in SCENARIOS
        if not scenario.staging_checks
    )

    covered_layers = sorted({layer for scenario in SCENARIOS for layer in scenario_layers(scenario)})
    missing_layers = sorted(set(REQUIRED_LAYERS) - set(covered_layers))
    missing_refs = missing_coverage_refs(repo_root)
    failures = [*scenario_failures, *missing_refs]
    if duplicate_ids:
        failures.append(f"duplicate scenario ids: {', '.join(duplicate_ids)}")
    if invalid_layers:
        failures.append(f"invalid layers: {', '.join(invalid_layers)}")
    if missing_layers:
        failures.append(f"missing required layers: {', '.join(missing_layers)}")

    return {
        "version": MATRIX_VERSION,
        "scenario_count": len(SCENARIOS),
        "required_layers": list(REQUIRED_LAYERS),
        "covered_layers": covered_layers,
        "missing_layers": missing_layers,
        "missing_coverage_refs": missing_refs,
        "scenario_failures": scenario_failures,
        "manual_signoff_required": True,
        "passed": not failures,
        "failures": failures,
    }


def build_report_payload(repo_root: Path = REPO_ROOT) -> dict[str, object]:
    return {
        "matrix": evaluate_matrix(repo_root),
        "scenarios": [asdict(scenario) for scenario in SCENARIOS],
    }


def build_markdown_report(repo_root: Path = REPO_ROOT) -> str:
    payload = build_report_payload(repo_root)
    matrix = payload["matrix"]
    lines = [
        "# Bot/WebApp Integration Scenario Matrix",
        "",
        f"- Version: `{matrix['version']}`",
        f"- Scenario count: `{matrix['scenario_count']}`",
        f"- Automated reference gate: `{'passed' if matrix['passed'] else 'failed'}`",
        "- Manual staging sign-off: required before production consideration",
        "",
    ]
    for scenario in SCENARIOS:
        lines.append(f"## {scenario.scenario_id} - {scenario.title}")
        lines.append(f"- Risk area: `{scenario.risk_area}`")
        lines.append("- Coverage refs:")
        for coverage in scenario.coverage_refs:
            lines.append(f"  - `{coverage.layer}` `{coverage.path}` -> `{coverage.snippet}`")
        lines.append("- Staging checks:")
        for check in scenario.staging_checks:
            lines.append(f"  - {check}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report and gate the Bot/WebApp integration Step 11 scenario matrix.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument("--markdown", action="store_true", help="Emit a markdown report.")
    parser.add_argument("--check", action="store_true", help="Fail if matrix coverage references are missing or malformed.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    payload = build_report_payload()

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.markdown:
        print(build_markdown_report(), end="")
    else:
        matrix = payload["matrix"]
        print("Bot/WebApp Integration Scenario Matrix")
        print(f"- Version: {matrix['version']}")
        print(f"- Scenario count: {matrix['scenario_count']}")
        print(f"- Required layers: {', '.join(matrix['required_layers'])}")
        print(f"- Covered layers: {', '.join(matrix['covered_layers'])}")
        print(f"- Automated reference gate: {'PASSED' if matrix['passed'] else 'FAILED'}")
        print("- Manual staging sign-off: REQUIRED")
        if matrix["failures"]:
            print("- Failures:")
            for failure in matrix["failures"]:
                print(f"  - {failure}")

    return 1 if args.check and not payload["matrix"]["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
