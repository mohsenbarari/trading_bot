"""Signed, immutable, no-skips contract for the authoritative three-site Matrix."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.dr_event_protocol import canonical_json_bytes
from core.secure_file_io import (
    read_secure_text,
    sha256_secure_file,
    verify_hash_chained_jsonl,
)


CAMPAIGN_SCHEMA = "three-site-staging-full-matrix-campaign-v1"
POLICY_SCHEMA = "three-site-staging-full-matrix-approver-policy-v1"
PHASE_EVIDENCE_SCHEMA = "three-site-staging-full-matrix-phase-v1"
SCENARIO_EVIDENCE_SCHEMA = "three-site-staging-full-matrix-scenario-v2"
OPERATION_EVIDENCE_SCHEMA = "three-site-staging-full-matrix-operation-v1"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ARTIFACT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,239}$")

CUSTOMER_ACTOR_PAIR_ASSERTION_PREFIX = "customer_actor_pair:"
CUSTOMER_ACTOR_PAIR_POLICIES: dict[str, str] = {
    "user__user": "positive_all_eligible_surfaces",
    "user__tier1_same_owner": "positive_all_eligible_surfaces",
    "user__tier2_same_owner": "positive_webapp_tier2_request_telegram_denied",
    "user__tier1_other_owner": "positive_all_eligible_surfaces",
    "user__tier2_other_owner": "positive_webapp_tier2_request_telegram_denied",
    "tier1__user_same_owner": "positive_all_eligible_surfaces",
    "tier2__user_same_owner": "negative_tier2_offer_creation_denied",
    "tier1__user_other_owner": "positive_all_eligible_surfaces",
    "tier2__user_other_owner": "negative_tier2_offer_creation_denied",
    "tier1__tier1_same_owner": "positive_all_eligible_surfaces",
    "tier1__tier2_same_owner": "positive_webapp_tier2_request_telegram_denied",
    "tier2__tier1_same_owner": "negative_tier2_offer_creation_denied",
    "tier2__tier2_same_owner": "negative_tier2_offer_creation_denied",
    "tier1__tier1_other_owner": "positive_all_eligible_surfaces",
    "tier1__tier2_other_owner": "positive_webapp_tier2_request_telegram_denied",
    "tier2__tier1_other_owner": "negative_tier2_offer_creation_denied",
    "tier2__tier2_other_owner": "negative_tier2_offer_creation_denied",
}
CUSTOMER_LIFECYCLE_MATRIX: dict[str, dict[str, str]] = {
    "customer_actor_matrix_normal_fi_active": {
        "runtime_state": "normal_fi_active",
        "webapp_writer": "webapp_fi",
        "public_origin": "webapp_fi",
        "connectivity": "stable",
        "cross_surface_policy": "execute_via_home_authority",
        "convergence_requirement": "steady_state_three_site_parity",
    },
    "customer_actor_matrix_iran_active_outage": {
        "runtime_state": "iran_active_outage",
        "webapp_writer": "webapp_ir",
        "public_origin": "webapp_ir",
        "connectivity": "iran_international_cutoff",
        "cross_surface_policy": "local_home_only_remote_home_mutation_denied",
        "convergence_requirement": "durable_local_commit_deferred_remote_delivery",
    },
    "customer_actor_matrix_recovery_ir_routed": {
        "runtime_state": "recovery_ir_routed",
        "webapp_writer": "webapp_ir",
        "public_origin": "webapp_ir",
        "connectivity": "recovering",
        "cross_surface_policy": "ir_authoritative_until_failback",
        "convergence_requirement": "catch_up_while_ir_continues_serving",
    },
    "customer_actor_matrix_post_failback_fi_active": {
        "runtime_state": "post_failback_fi_active",
        "webapp_writer": "webapp_fi",
        "public_origin": "webapp_fi",
        "connectivity": "restored_stable",
        "cross_surface_policy": "execute_via_home_authority",
        "convergence_requirement": "final_three_site_database_blob_effect_parity",
    },
}


def customer_actor_pair_assertion_name(actor_pair: str) -> str:
    if actor_pair not in CUSTOMER_ACTOR_PAIR_POLICIES:
        raise FullMatrixCampaignError("Full Matrix customer actor pair is unknown")
    return f"{CUSTOMER_ACTOR_PAIR_ASSERTION_PREFIX}{actor_pair}"


def customer_actor_pair_contracts(scenario_id: str) -> dict[str, dict[str, Any]]:
    """Return the source-owned 17-cell oracle contract for one lifecycle state."""

    state = CUSTOMER_LIFECYCLE_MATRIX.get(scenario_id)
    if state is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for actor_pair, execution_policy in CUSTOMER_ACTOR_PAIR_POLICIES.items():
        if execution_policy == "positive_all_eligible_surfaces":
            required_result = "eligible_surface_trade_completed"
            policy_invariants = [
                "owner_routing_preserved",
                "counterparty_privacy_preserved",
                "recipient_policy_matched",
                "terminal_request_ledger_recorded",
                "zero_duplicate_trade_or_delivery",
            ]
        elif execution_policy == "positive_webapp_tier2_request_telegram_denied":
            required_result = "webapp_trade_completed_and_telegram_request_denied"
            policy_invariants = [
                "owner_routing_preserved",
                "counterparty_privacy_preserved",
                "tier2_webapp_only_policy_preserved",
                "recipient_policy_matched",
                "terminal_request_ledger_recorded",
                "zero_duplicate_trade_or_delivery",
            ]
        else:
            required_result = "tier2_offer_creation_denied_with_zero_mutation"
            policy_invariants = [
                "zero_offer_created",
                "zero_publication_intent_created",
                "zero_trade_or_request_created",
                "zero_notification_or_channel_side_effect",
            ]
        result[customer_actor_pair_assertion_name(actor_pair)] = {
            "actor_pair": actor_pair,
            "execution_policy": execution_policy,
            "required_result": required_result,
            "runtime_state": state["runtime_state"],
            "webapp_writer": state["webapp_writer"],
            "public_origin": state["public_origin"],
            "telegram_authority": "bot_fi",
            "connectivity": state["connectivity"],
            "cross_surface_policy": state["cross_surface_policy"],
            "convergence_requirement": state["convergence_requirement"],
            "required_invariants": policy_invariants,
        }
    return result

PHASE_SCENARIOS: dict[str, tuple[str, ...]] = {
    "migration_topology": (
        "fresh_main_queue_dr_histories_equal",
        "legacy_staging_clone_migrated",
        "least_privilege_roles_attested",
        "four_role_identity_isolated",
        "backup_restore_rehearsed",
        "legacy_rollback_rehearsed",
        "integer_id_collision_fixtures",
        "natural_identity_cross_site_collision",
        "unique_ids_real_business_conflict_quarantined",
        "counter_double_increment_fixture",
        "delete_update_resurrection_fixture",
        "backup_counts_pass_semantic_parity_fails",
    ),
    "combined_workload": (
        "bot_and_webapp_offers_concurrent",
        "requests_trades_partial_settlement",
        "notifications_webpush_messenger_files",
        "queue_publication_edit_callback_private",
        "writer_renewal_and_dr_relay_under_load",
        "relay_preserves_origin_without_echo",
        "dropped_wakeup_still_durably_drains",
        "ambiguous_client_command_retry_is_idempotent",
        "customer_actor_matrix_normal_fi_active",
    ),
    "queue_faults": (
        "enqueue_commit_crash_boundaries",
        "claim_limiter_provider_crash_boundaries",
        "provider_success_outcome_ambiguity",
        "reconciliation_owner_loss_restart",
        "rate_limit_timeout_malformed_response",
        "duplicate_worker_stale_owner_redis_loss",
        "runtime_cutover_and_forward_rollback",
    ),
    "dr_faults": (
        "business_event_delivery_commit_boundaries",
        "receive_ack_apply_checkpoint_boundaries",
        "duplicate_gap_out_of_order_replay",
        "same_sequence_hash_conflict_quarantine",
        "transaction_group_partial_and_corrupt",
        "stale_term_terminal_and_destructive_rejected",
        "blob_database_asymmetric_failure_resume",
        "destination_sequence_private_gap_regression",
        "same_event_replay_is_idempotent",
        "table_priority_cannot_overtake_stream_sequence",
        "acknowledged_source_event_absent_target_blocks_promotion",
        "missing_or_corrupt_blob_blocks_readiness",
    ),
    "partitions_failover": (
        "bot_fi_webapp_fi_partition",
        "webapp_fi_webapp_ir_partition",
        "witness_partition_and_vm_pause",
        "asymmetric_ack_both_directions",
        "object_storage_interruption",
        "arvan_control_failure_rate_limit",
        "fi_host_loss_without_national_cutoff",
        "iran_international_cutoff_promotes_ir",
        "simultaneous_promotion_attempt_single_epoch",
        "controller_restart_each_failover_cutpoint",
        "queue_work_inflight_during_promotion",
        "customer_actor_matrix_iran_active_outage",
        "arvan_pop_split_origin_is_safe",
        "certificate_expiry_during_national_outage",
        "dns_global_national_asymmetry",
        "deployment_or_migration_during_transition_rejected",
        "permanent_fi_recovery_hub_loss",
        "ir_only_active_origin_loss_is_safe_unavailable",
        "power_loss_between_fence_and_enable",
        "duplicate_operator_commands_race",
        "controller_restart_mid_arvan_mutation",
    ),
    "recovery_failback": (
        "short_medium_long_outage_rules",
        "bot_remains_active_all_outage_classes",
        "ir_remains_active_during_recovery",
        "customer_actor_matrix_recovery_ir_routed",
        "reconnect_flap_and_bounded_catchup",
        "applied_checkpoint_conflict_effect_gates",
        "database_and_blob_final_parity",
        "final_write_barrier_with_live_arrivals",
        "fi_epoch_reacquire_and_route_switch",
        "old_http_websocket_connections_drained",
        "recovery_and_failback_restart_resume",
        "file_transfer_interruption_resumes_by_hash",
        "database_blob_inverse_completion_reconciles",
        "customer_actor_matrix_post_failback_fi_active",
    ),
    "security_isolation": (
        "set_role_and_cross_role_access_denied",
        "fake_event_and_raw_sql_bypass_denied",
        "cross_service_secret_boundaries",
        "wrong_pairwise_identity_and_nonce_replay",
        "hostile_artifact_path_and_signature_denied",
        "production_host_domain_bucket_untouched",
        "expired_plan_only_safe_fenced_recovery",
        "protocol_schema_key_rotation_mismatch",
        "restored_old_epoch_effects_remain_fenced",
        "startup_mutation_on_fenced_standby_rejected",
    ),
    "capacity_dpi": (
        "three_hundred_rps_fifty_fifty",
        "finland_directions_one_fifty_events_each",
        "webapp_dr_three_hundred_events_amplified",
        "batch_flush_inflight_boundaries",
        "database_redis_blob_storage_watermarks",
        "dpi_request_byte_budget_enforced",
        "one_hour_backlog_with_live_traffic",
        "recovery_eta_and_non_starvation",
        "twenty_four_hour_endurance_no_growth",
        "healthy_link_never_accumulates_backlog",
        "wal_event_redis_blob_capacity_exhaustion_safe",
    ),
    "application_regression": (
        "canonical_staging_domain_auth_cors_links",
        "market_trade_account_admin_regression",
        "messenger_upload_download_regression",
        "websocket_reconnect_and_cursor_reconcile",
        "cdn_dynamic_cache_and_stale_health_denied",
        "session_failover_contract",
        "test_ingress_same_release_and_data_plane",
    ),
    "cleanup_repeatability": (
        "queue_jobs_effects_conflicts_reconciled",
        "writer_epoch_route_and_standby_final_state",
        "temporary_faults_networks_processes_removed",
        "artifact_hash_chain_and_external_anchor",
        "production_boundaries_reverified",
        "second_cycle_same_or_stronger_oracles",
    ),
}

PHASES = tuple(PHASE_SCENARIOS)
BOUND_ARTIFACTS = frozenset(
    {
        "provisioned_inventory",
        "inventory_approval",
        "inventory_signer_policy",
        "migration_plan",
        "migration_approval",
        "source_freeze_bot_fi",
        "source_freeze_webapp_fi",
        "source_backup_bot_fi",
        "source_backup_webapp_fi",
        "seed_manifest_bot_fi",
        "seed_manifest_webapp_fi",
        "image_inventory_bot_fi",
        "image_inventory_webapp_fi",
        "image_inventory_webapp_ir",
        "image_inventory_witness",
        "global_commit",
        "campaign_bundle",
        "queue_activation_transition",
        "failover_backend_config",
        "full_matrix_backend_config",
    }
)


class FullMatrixCampaignError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FullMatrixCampaignError("Full Matrix JSON contains a duplicate key")
        result[key] = value
    return result


def secure_json(path: Path, *, label: str, max_size: int = 4 * 1024 * 1024) -> dict[str, Any]:
    try:
        value = json.loads(
            read_secure_text(path, label=label, max_size=max_size),
            object_pairs_hook=_strict_object,
        )
    except Exception as exc:
        raise FullMatrixCampaignError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise FullMatrixCampaignError(f"{label} must be an object")
    return value


def _utc(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise FullMatrixCampaignError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise FullMatrixCampaignError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _matrix_operation_id(
    campaign_hash: str,
    kind: str,
    *,
    phase: str = "",
    scenario_id: str = "",
    iteration: int = 0,
    failed: bool | None = None,
    attempt: int = 0,
) -> str:
    material = (
        f"{campaign_hash}:{kind}:{iteration}:{phase}:{scenario_id}:"
        f"{'' if failed is None else str(failed).lower()}:{attempt}"
    )
    return str(uuid5(NAMESPACE_URL, material))


def _policy(payload: dict[str, Any], *, release_sha: str) -> tuple[dict[str, tuple[str, str, bytes]], str]:
    fields = {"schema", "policy_id", "release_sha", "minimum_approvals", "signers"}
    if (
        set(payload) != fields
        or payload.get("schema") != POLICY_SCHEMA
        or payload.get("release_sha") != release_sha
        or payload.get("minimum_approvals") != 2
        or not isinstance(payload.get("signers"), list)
    ):
        raise FullMatrixCampaignError("Full Matrix approver policy is invalid")
    try:
        UUID(str(payload["policy_id"]))
    except ValueError as exc:
        raise FullMatrixCampaignError("Full Matrix policy_id is invalid") from exc
    result: dict[str, tuple[str, str, bytes]] = {}
    operators: set[str] = set()
    custody: set[str] = set()
    public_keys: set[bytes] = set()
    for item in payload["signers"]:
        if not isinstance(item, dict) or set(item) != {
            "operator", "key_id", "custody_domain", "public_key"
        }:
            raise FullMatrixCampaignError("Full Matrix signer fields are invalid")
        operator = str(item["operator"]).strip()
        key_id = str(item["key_id"]).strip()
        domain = str(item["custody_domain"]).strip()
        try:
            public_key = base64.b64decode(str(item["public_key"]), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise FullMatrixCampaignError("Full Matrix signer key is invalid") from exc
        if (
            not operator or operator in operators or not key_id or key_id in result
            or not domain or domain in custody or len(public_key) != 32
            or public_key in public_keys
        ):
            raise FullMatrixCampaignError("Full Matrix signers are not independent")
        result[key_id] = (operator, domain, public_key)
        operators.add(operator)
        custody.add(domain)
        public_keys.add(public_key)
    if len(result) < 2:
        raise FullMatrixCampaignError("Full Matrix policy needs two independent signers")
    return result, hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def verify_campaign(
    campaign: dict[str, Any],
    *,
    approver_policy: dict[str, Any],
    now: datetime | None = None,
    allow_expired_for_safe_cleanup: bool = False,
) -> dict[str, Any]:
    fields = {
        "schema", "campaign_id", "generated_at", "expires_at", "baseline_sha",
        "activation_sha", "release_sha", "official_staging_url", "failover_test_url",
        "object_storage", "repetitions", "required_phases", "required_scenarios",
        "no_skips", "cleanup_required", "production_forbidden", "bound_artifacts",
        "approver_policy_hash", "approvals",
    }
    if set(campaign) != fields or campaign.get("schema") != CAMPAIGN_SCHEMA:
        raise FullMatrixCampaignError("Full Matrix campaign fields/schema are invalid")
    try:
        campaign_id = str(UUID(str(campaign["campaign_id"])))
    except ValueError as exc:
        raise FullMatrixCampaignError("Full Matrix campaign_id is invalid") from exc
    baseline = str(campaign["baseline_sha"])
    activation = str(campaign["activation_sha"])
    release = str(campaign["release_sha"])
    if (
        SHA40.fullmatch(baseline) is None
        or SHA40.fullmatch(activation) is None
        or activation == baseline
        or release != activation
    ):
        raise FullMatrixCampaignError("Full Matrix release lineage is invalid")
    if (
        campaign["official_staging_url"] != "https://staging.gold-trade.ir"
        or campaign["failover_test_url"] != "https://app.gold-trading.ir"
        or campaign["required_phases"] != list(PHASES)
        or campaign["required_scenarios"]
        != {phase: list(scenarios) for phase, scenarios in PHASE_SCENARIOS.items()}
        or type(campaign["repetitions"]) is not int
        or campaign["repetitions"] != 2
        or campaign["no_skips"] is not True
        or campaign["cleanup_required"] is not True
        or campaign["production_forbidden"] is not True
    ):
        raise FullMatrixCampaignError("Full Matrix scope/no-skips contract is invalid")
    storage = campaign["object_storage"]
    if (
        not isinstance(storage, dict)
        or set(storage) != {"region", "bucket", "prefix", "versioned", "private"}
        or storage.get("region") != "ir-thr-at1"
        or not str(storage.get("bucket") or "").startswith("staging-")
        or not str(storage.get("prefix") or "").startswith(f"full-matrix/{campaign_id}/")
        or storage.get("versioned") is not True
        or storage.get("private") is not True
    ):
        raise FullMatrixCampaignError("Full Matrix Object Storage scope is invalid")
    artifacts = campaign["bound_artifacts"]
    if (
        not isinstance(artifacts, dict)
        or set(artifacts) != BOUND_ARTIFACTS
        or any(SHA256.fullmatch(str(value)) is None for value in artifacts.values())
    ):
        raise FullMatrixCampaignError("Full Matrix bound artifact hashes are invalid")
    generated = _utc(campaign["generated_at"], label="campaign generated_at")
    expires = _utc(campaign["expires_at"], label="campaign expires_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if (
        generated > current + timedelta(minutes=2)
        or (expires <= current and not allow_expired_for_safe_cleanup)
        or expires <= generated
        or expires - generated > timedelta(hours=72)
    ):
        raise FullMatrixCampaignError("Full Matrix campaign is expired or too long")
    signers, policy_hash = _policy(approver_policy, release_sha=release)
    if campaign["approver_policy_hash"] != policy_hash:
        raise FullMatrixCampaignError("Full Matrix campaign is not bound to approver policy")
    approvals = campaign["approvals"]
    if not isinstance(approvals, list) or len(approvals) != 2:
        raise FullMatrixCampaignError("Full Matrix campaign needs exactly two approvals")
    unsigned = {key: value for key, value in campaign.items() if key != "approvals"}
    campaign_hash = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    used_operators: set[str] = set()
    used_domains: set[str] = set()
    for approval in approvals:
        if not isinstance(approval, dict) or set(approval) != {
            "operator", "key_id", "signature"
        }:
            raise FullMatrixCampaignError("Full Matrix approval fields are invalid")
        key_id = str(approval["key_id"])
        signer = signers.get(key_id)
        operator = str(approval["operator"])
        if (
            signer is None or operator != signer[0] or operator in used_operators
            or signer[1] in used_domains
        ):
            raise FullMatrixCampaignError("Full Matrix approvals are not independent")
        try:
            signature = base64.b64decode(str(approval["signature"]), validate=True)
            Ed25519PublicKey.from_public_bytes(signer[2]).verify(
                signature, campaign_hash.encode("ascii")
            )
        except (ValueError, binascii.Error, InvalidSignature) as exc:
            raise FullMatrixCampaignError("Full Matrix approval signature is invalid") from exc
        used_operators.add(operator)
        used_domains.add(signer[1])
    return {
        "status": "approved",
        "campaign_id": campaign_id,
        "campaign_hash": campaign_hash,
        "release_sha": release,
        "activation_sha": activation,
        "repetitions": campaign["repetitions"],
        "expires_at": expires.isoformat(),
    }


def verify_bound_artifacts(
    campaign: dict[str, Any], mappings: dict[str, Path]
) -> dict[str, dict[str, Any]]:
    if set(mappings) != BOUND_ARTIFACTS:
        raise FullMatrixCampaignError("Full Matrix bound artifact mapping is incomplete")
    result: dict[str, dict[str, Any]] = {}
    for name, path in sorted(mappings.items()):
        digest, size = sha256_secure_file(path, label=f"Full Matrix {name}")
        if digest != campaign["bound_artifacts"][name] or size == 0:
            raise FullMatrixCampaignError(f"Full Matrix bound artifact differs: {name}")
        result[name] = {"sha256": digest, "size": size}
    return result


def _relative_artifact(value: Any) -> str:
    text = str(value or "")
    path = PurePosixPath(text)
    if (
        SAFE_ARTIFACT.fullmatch(text) is None
        or path.is_absolute()
        or len(path.parts) != 1
        or ".." in path.parts
        or "." in path.parts
    ):
        raise FullMatrixCampaignError("Full Matrix artifact path is unsafe")
    return text


def verify_scenario_evidence(
    evidence: dict[str, Any],
    *,
    campaign: dict[str, Any],
    campaign_hash: str,
    phase: str,
    scenario_id: str,
    iteration: int,
    attempt: int,
    operation_id: str,
    artifact_root: Path,
) -> dict[str, Any]:
    """Re-open and semantically validate one retained scenario artifact.

    The backend is not allowed to reduce a scenario to a boolean and a hash.
    Every result has a closed oracle identity, four mandatory assertions, and
    retained raw evidence files that can be independently re-hashed later.
    """

    fields = {
        "schema", "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "phase", "scenario_id", "iteration", "oracle_id",
        "operation_id", "attempt",
        "started_at", "finished_at", "duration_seconds", "assertions",
        "evidence_refs", "cleanup_residue_count", "production_touched",
    }
    if set(evidence) != fields or evidence.get("schema") != SCENARIO_EVIDENCE_SCHEMA:
        raise FullMatrixCampaignError("Full Matrix scenario evidence schema is invalid")
    if (
        evidence.get("status") != "passed"
        or evidence.get("campaign_id") != campaign["campaign_id"]
        or evidence.get("campaign_hash") != campaign_hash
        or evidence.get("release_sha") != campaign["release_sha"]
        or evidence.get("activation_sha") != campaign["activation_sha"]
        or evidence.get("phase") != phase
        or evidence.get("scenario_id") != scenario_id
        or evidence.get("iteration") != iteration
        or evidence.get("attempt") != attempt
        or evidence.get("operation_id") != operation_id
        or evidence.get("oracle_id") != f"{phase}.{scenario_id}.v1"
        or evidence.get("production_touched") is not False
        or evidence.get("cleanup_residue_count") != 0
    ):
        raise FullMatrixCampaignError("Full Matrix scenario identity/status is invalid")
    started = _utc(evidence["started_at"], label="scenario started_at")
    finished = _utc(evidence["finished_at"], label="scenario finished_at")
    duration = evidence["duration_seconds"]
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or duration < 0
        or started > finished
        or (finished - started).total_seconds() + 2 < float(duration)
    ):
        raise FullMatrixCampaignError("Full Matrix scenario duration is invalid")
    if scenario_id == "twenty_four_hour_endurance_no_growth" and duration < 86400:
        raise FullMatrixCampaignError("Full Matrix endurance scenario ran under 24 hours")

    references = evidence["evidence_refs"]
    if not isinstance(references, list) or not references:
        raise FullMatrixCampaignError("Full Matrix scenario has no raw evidence")
    retained: dict[str, dict[str, Any]] = {}
    for item in references:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise FullMatrixCampaignError("Full Matrix raw evidence reference is malformed")
        relative = _relative_artifact(item["path"])
        if (
            relative in retained
            or SHA256.fullmatch(str(item["sha256"])) is None
            or type(item["size"]) is not int
            or item["size"] <= 0
        ):
            raise FullMatrixCampaignError("Full Matrix raw evidence identity is invalid")
        try:
            digest, size = sha256_secure_file(
                artifact_root / relative,
                label=f"Full Matrix {phase}/{scenario_id} raw evidence",
            )
        except Exception as exc:
            raise FullMatrixCampaignError(
                "Full Matrix raw scenario evidence is missing or unsafe"
            ) from exc
        if digest != item["sha256"] or size != item["size"]:
            raise FullMatrixCampaignError("Full Matrix raw scenario evidence differs")
        retained[relative] = {"path": relative, "sha256": digest, "size": size}

    assertions = evidence["assertions"]
    if not isinstance(assertions, list) or len(assertions) < 3:
        raise FullMatrixCampaignError("Full Matrix scenario assertions are incomplete")
    required = {
        "operation_executed",
        "expected_outcome",
        "production_boundary",
        f"oracle:{scenario_id}",
    }
    customer_contracts = customer_actor_pair_contracts(scenario_id)
    required.update(customer_contracts)
    if scenario_id == "twenty_four_hour_endurance_no_growth":
        required.add("minimum_duration")
    seen: set[str] = set()
    used_refs: set[str] = set()
    customer_pair_refs: set[str] = set()
    for assertion in assertions:
        if not isinstance(assertion, dict) or set(assertion) != {
            "name", "status", "expected", "observed", "evidence_refs"
        }:
            raise FullMatrixCampaignError("Full Matrix scenario assertion is malformed")
        name = str(assertion["name"])
        refs = assertion["evidence_refs"]
        if (
            not name
            or name in seen
            or assertion["status"] != "passed"
            or not isinstance(refs, list)
            or not refs
        ):
            raise FullMatrixCampaignError("Full Matrix scenario assertion did not pass")
        normalized = {_relative_artifact(item) for item in refs}
        if not normalized.issubset(retained):
            raise FullMatrixCampaignError("Full Matrix assertion references missing evidence")
        if name == "production_boundary" and (
            assertion["expected"] is not False or assertion["observed"] is not False
        ):
            raise FullMatrixCampaignError("Full Matrix production boundary was not preserved")
        if name == "operation_executed" and (
            assertion["expected"] != {
                "operation_id": operation_id,
                "scenario_id": scenario_id,
                "iteration": iteration,
                "attempt": attempt,
            }
            or assertion["observed"] != assertion["expected"]
        ):
            raise FullMatrixCampaignError("Full Matrix scenario operation was not proven")
        if name == "expected_outcome" and (
            not isinstance(assertion["expected"], dict)
            or not assertion["expected"]
            or assertion["observed"] != assertion["expected"]
        ):
            raise FullMatrixCampaignError("Full Matrix expected outcome differs from observation")
        if name == f"oracle:{scenario_id}" and (
            not isinstance(assertion["expected"], dict)
            or not assertion["expected"]
            or assertion["observed"] != assertion["expected"]
        ):
            raise FullMatrixCampaignError("Full Matrix independent oracle did not match")
        if name in customer_contracts:
            if (
                assertion["expected"] != customer_contracts[name]
                or assertion["observed"] != customer_contracts[name]
                or len(normalized) != 1
                or not customer_pair_refs.isdisjoint(normalized)
            ):
                raise FullMatrixCampaignError(
                    "Full Matrix customer actor-pair lifecycle proof is invalid"
                )
            customer_pair_refs.update(normalized)
        if name == "minimum_duration" and (
            assertion["expected"] != 86400
            or not isinstance(assertion["observed"], (int, float))
            or isinstance(assertion["observed"], bool)
            or assertion["observed"] < 86400
        ):
            raise FullMatrixCampaignError("Full Matrix duration assertion is invalid")
        seen.add(name)
        used_refs.update(normalized)
    if not required.issubset(seen) or used_refs != set(retained):
        raise FullMatrixCampaignError("Full Matrix scenario oracle coverage is incomplete")
    if customer_contracts and len(customer_pair_refs) != len(customer_contracts):
        raise FullMatrixCampaignError(
            "Full Matrix customer actor-pair raw evidence is incomplete"
        )
    return {
        "assertion_count": len(assertions),
        "duration_seconds": float(duration),
        "raw_artifacts": [retained[path] for path in sorted(retained)],
        "artifact_paths": set(retained),
    }


def verify_operation_evidence(
    evidence: dict[str, Any],
    *,
    campaign: dict[str, Any],
    campaign_hash: str,
    operation_kind: str,
    operation_id: str,
    operation_context: dict[str, Any],
    artifact_root: Path,
) -> dict[str, Any]:
    """Validate non-scenario operation evidence instead of trusting a pass bit."""

    fields = {
        "schema", "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "operation_kind", "operation_id", "operation_context",
        "assertions", "evidence_refs", "residue_count", "production_touched",
    }
    if set(evidence) != fields or evidence.get("schema") != OPERATION_EVIDENCE_SCHEMA:
        raise FullMatrixCampaignError("Full Matrix operation evidence schema is invalid")
    if (
        evidence.get("status") != "passed"
        or evidence.get("campaign_id") != campaign["campaign_id"]
        or evidence.get("campaign_hash") != campaign_hash
        or evidence.get("release_sha") != campaign["release_sha"]
        or evidence.get("activation_sha") != campaign["activation_sha"]
        or evidence.get("operation_kind") != operation_kind
        or evidence.get("operation_id") != operation_id
        or evidence.get("operation_context") != operation_context
        or evidence.get("production_touched") is not False
        or evidence.get("residue_count") != 0
    ):
        raise FullMatrixCampaignError("Full Matrix operation identity/status is invalid")
    required_by_kind = {
        "preflight": {
            "campaign_identity_bound", "prerequisites_verified", "topology_ready",
            "production_boundary",
        },
        "recovery": {
            "faults_removed", "writer_state_safe", "residue_zero",
            "production_boundary",
        },
        "cleanup": {
            "faults_removed", "writer_state_safe", "residue_zero",
            "production_boundary",
        },
        "finalize": {
            "all_faults_removed", "writer_state_safe", "residue_zero",
            "production_boundary",
        },
    }
    required = required_by_kind.get(operation_kind)
    if required is None:
        raise FullMatrixCampaignError("Full Matrix operation kind is invalid")
    references = evidence.get("evidence_refs")
    if not isinstance(references, list) or not references:
        raise FullMatrixCampaignError("Full Matrix operation has no raw evidence")
    retained: dict[str, dict[str, Any]] = {}
    for item in references:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise FullMatrixCampaignError(
                "Full Matrix operation evidence reference is malformed"
            )
        relative = _relative_artifact(item["path"])
        if (
            relative in retained
            or SHA256.fullmatch(str(item["sha256"])) is None
            or type(item["size"]) is not int
            or item["size"] <= 0
        ):
            raise FullMatrixCampaignError(
                "Full Matrix operation evidence identity is invalid"
            )
        digest, size = sha256_secure_file(
            artifact_root / relative,
            label=f"Full Matrix {operation_kind} raw evidence",
        )
        if digest != item["sha256"] or size != item["size"]:
            raise FullMatrixCampaignError("Full Matrix operation raw evidence differs")
        retained[relative] = {"path": relative, "sha256": digest, "size": size}
    assertions = evidence.get("assertions")
    if not isinstance(assertions, list) or len(assertions) != len(required):
        raise FullMatrixCampaignError("Full Matrix operation assertions are incomplete")
    seen: set[str] = set()
    used_refs: set[str] = set()
    for assertion in assertions:
        if not isinstance(assertion, dict) or set(assertion) != {
            "name", "status", "expected", "observed", "evidence_refs"
        }:
            raise FullMatrixCampaignError("Full Matrix operation assertion is malformed")
        name = str(assertion["name"])
        refs = assertion["evidence_refs"]
        if (
            name not in required
            or name in seen
            or assertion["status"] != "passed"
            or assertion["observed"] != assertion["expected"]
            or not isinstance(refs, list)
            or not refs
        ):
            raise FullMatrixCampaignError("Full Matrix operation assertion did not pass")
        normalized = {_relative_artifact(item) for item in refs}
        if not normalized.issubset(retained):
            raise FullMatrixCampaignError(
                "Full Matrix operation assertion lacks raw evidence"
            )
        if name == "production_boundary" and assertion["expected"] is not False:
            raise FullMatrixCampaignError(
                "Full Matrix production boundary was not preserved"
            )
        if name == "residue_zero" and assertion["expected"] != 0:
            raise FullMatrixCampaignError("Full Matrix operation residue is not zero")
        if name not in {"production_boundary", "residue_zero"} and (
            assertion["expected"] is not True
        ):
            raise FullMatrixCampaignError(
                "Full Matrix operation safety assertion is invalid"
            )
        seen.add(name)
        used_refs.update(normalized)
    if seen != required or used_refs != set(retained):
        raise FullMatrixCampaignError(
            "Full Matrix operation oracle coverage is incomplete"
        )
    return {
        "assertion_count": len(assertions),
        "raw_artifacts": [retained[path] for path in sorted(retained)],
        "artifact_paths": set(retained),
    }


def _validate_artifact_root(path: Path) -> None:
    if not path.is_absolute():
        raise FullMatrixCampaignError("Full Matrix artifact root must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise FullMatrixCampaignError("Full Matrix artifact root is unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise FullMatrixCampaignError(
                "Full Matrix artifact root must be owner-only"
            )
    finally:
        os.close(descriptor)


def verify_phase_evidence(
    evidence: dict[str, Any],
    *,
    campaign: dict[str, Any],
    campaign_hash: str,
    artifact_root: Path,
) -> dict[str, Any]:
    fields = {
        "schema", "status", "campaign_id", "campaign_hash", "release_sha",
        "activation_sha", "phase", "iteration", "started_at", "finished_at",
        "scenario_results", "skip_count", "production_touched", "artifacts",
        "cleanup_residue_count",
    }
    if set(evidence) != fields or evidence.get("schema") != PHASE_EVIDENCE_SCHEMA:
        raise FullMatrixCampaignError("Full Matrix phase evidence fields/schema are invalid")
    phase = str(evidence.get("phase"))
    iteration = evidence.get("iteration")
    if (
        evidence.get("status") != "passed"
        or evidence.get("campaign_id") != campaign["campaign_id"]
        or evidence.get("campaign_hash") != campaign_hash
        or evidence.get("release_sha") != campaign["release_sha"]
        or evidence.get("activation_sha") != campaign["activation_sha"]
        or phase not in PHASE_SCENARIOS
        or type(iteration) is not int
        or not 1 <= iteration <= campaign["repetitions"]
        or evidence.get("skip_count") != 0
        or evidence.get("production_touched") is not False
    ):
        raise FullMatrixCampaignError("Full Matrix phase identity/status is invalid")
    started = _utc(evidence["started_at"], label="phase started_at")
    finished = _utc(evidence["finished_at"], label="phase finished_at")
    campaign_start = _utc(campaign["generated_at"], label="campaign generated_at")
    campaign_end = _utc(campaign["expires_at"], label="campaign expires_at")
    if not campaign_start <= started <= finished <= campaign_end:
        raise FullMatrixCampaignError("Full Matrix phase time is outside campaign window")
    scenarios = evidence["scenario_results"]
    expected = PHASE_SCENARIOS[phase]
    if not isinstance(scenarios, list) or len(scenarios) != len(expected):
        raise FullMatrixCampaignError("Full Matrix phase scenario cardinality is invalid")
    seen: set[str] = set()
    scenario_artifact_paths: set[str] = set()
    scenario_raw_paths: set[str] = set()
    for result in scenarios:
        if not isinstance(result, dict) or set(result) != {
            "scenario_id", "status", "assertion_count", "evidence_hash",
            "duration_seconds", "artifact", "operation_id", "attempt",
        }:
            raise FullMatrixCampaignError("Full Matrix scenario result is malformed")
        scenario_id = str(result["scenario_id"])
        artifact = result["artifact"]
        if (
            scenario_id not in expected or scenario_id in seen
            or result["status"] != "passed"
            or type(result["assertion_count"]) is not int
            or result["assertion_count"] < 1
            or SHA256.fullmatch(str(result["evidence_hash"])) is None
            or not isinstance(result.get("operation_id"), str)
            or type(result.get("attempt")) is not int
            or result["attempt"] < 1
            or not isinstance(result["duration_seconds"], (int, float))
            or isinstance(result["duration_seconds"], bool)
            or not isinstance(artifact, dict)
            or set(artifact) != {"path", "sha256", "size"}
        ):
            raise FullMatrixCampaignError("Full Matrix scenario did not pass exactly once")
        relative = _relative_artifact(artifact["path"])
        if (
            relative in scenario_artifact_paths
            or artifact["sha256"] != result["evidence_hash"]
            or type(artifact["size"]) is not int
            or artifact["size"] <= 0
        ):
            raise FullMatrixCampaignError("Full Matrix scenario artifact record is invalid")
        digest, size = sha256_secure_file(
            artifact_root / relative,
            label="Full Matrix retained scenario artifact",
        )
        if digest != artifact["sha256"] or size != artifact["size"]:
            raise FullMatrixCampaignError("Full Matrix retained scenario artifact differs")
        scenario_evidence = secure_json(
            artifact_root / relative,
            label="Full Matrix retained scenario evidence",
        )
        verified = verify_scenario_evidence(
            scenario_evidence,
            campaign=campaign,
            campaign_hash=campaign_hash,
            phase=phase,
            scenario_id=scenario_id,
            iteration=iteration,
            attempt=result["attempt"],
            operation_id=result["operation_id"],
            artifact_root=artifact_root,
        )
        if (
            verified["assertion_count"] != result["assertion_count"]
            or verified["duration_seconds"] != float(result["duration_seconds"])
        ):
            raise FullMatrixCampaignError("Full Matrix scenario summary differs from evidence")
        if scenario_raw_paths.intersection(verified["artifact_paths"]):
            raise FullMatrixCampaignError("Full Matrix raw evidence was reused by scenarios")
        scenario_artifact_paths.add(relative)
        scenario_raw_paths.update(verified["artifact_paths"])
        seen.add(scenario_id)
    if seen != set(expected):
        raise FullMatrixCampaignError("Full Matrix required scenarios are incomplete")
    cleanup_count = evidence["cleanup_residue_count"]
    if phase == "cleanup_repeatability":
        if cleanup_count != 0:
            raise FullMatrixCampaignError("Full Matrix cleanup left residue")
    elif cleanup_count is not None:
        raise FullMatrixCampaignError("cleanup residue is valid only for cleanup phase")
    artifacts = evidence["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise FullMatrixCampaignError("Full Matrix phase has no retained artifacts")
    artifact_records: list[dict[str, Any]] = []
    used_paths: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}:
            raise FullMatrixCampaignError("Full Matrix artifact record is malformed")
        relative = _relative_artifact(item["path"])
        if relative in used_paths or type(item["size"]) is not int or item["size"] <= 0:
            raise FullMatrixCampaignError("Full Matrix artifact identity/size is invalid")
        path = artifact_root / relative
        digest, size = sha256_secure_file(path, label="Full Matrix phase artifact")
        if digest != item["sha256"] or size != item["size"]:
            raise FullMatrixCampaignError("Full Matrix retained artifact hash/size differs")
        used_paths.add(relative)
        artifact_records.append({"path": relative, "sha256": digest, "size": size})
    required_artifacts = scenario_artifact_paths | scenario_raw_paths
    if not required_artifacts.issubset(used_paths):
        raise FullMatrixCampaignError(
            "Full Matrix phase does not retain every scenario/raw artifact"
        )
    evidence_hash = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
    return {
        "phase": phase,
        "iteration": iteration,
        "evidence_hash": evidence_hash,
        "artifact_count": len(artifact_records),
        "assertion_count": sum(item["assertion_count"] for item in scenarios),
        "artifact_paths": sorted(used_paths),
    }


def verify_complete_matrix(
    *,
    campaign: dict[str, Any],
    approver_policy: dict[str, Any],
    bound_artifacts: dict[str, Path],
    phase_evidence: list[dict[str, Any]],
    artifact_root: Path,
    execution_journal: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    # Evidence verification is an audit operation, not authorization for new
    # live work.  A completed campaign must remain independently verifiable
    # after its execution window expires; journal timestamps below still have
    # to fall inside the signed campaign window.
    approved = verify_campaign(
        campaign,
        approver_policy=approver_policy,
        now=now,
        allow_expired_for_safe_cleanup=True,
    )
    _validate_artifact_root(artifact_root)
    bindings = verify_bound_artifacts(campaign, bound_artifacts)
    expected_keys = {
        (phase, iteration)
        for iteration in range(1, campaign["repetitions"] + 1)
        for phase in PHASES
    }
    results: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, int]] = set()
    used_artifacts: set[str] = set()
    for evidence in phase_evidence:
        result = verify_phase_evidence(
            evidence,
            campaign=campaign,
            campaign_hash=approved["campaign_hash"],
            artifact_root=artifact_root,
        )
        key = (result["phase"], result["iteration"])
        if key in seen_keys:
            raise FullMatrixCampaignError("Full Matrix phase/iteration evidence is duplicated")
        overlap = used_artifacts.intersection(result["artifact_paths"])
        if overlap:
            raise FullMatrixCampaignError("Full Matrix artifact was reused across phase evidence")
        seen_keys.add(key)
        used_artifacts.update(result.pop("artifact_paths"))
        results.append(result)
    if seen_keys != expected_keys:
        raise FullMatrixCampaignError("Full Matrix has missing phase/iteration evidence")
    ordered = sorted(results, key=lambda item: (item["iteration"], PHASES.index(item["phase"])))
    journal_binding: dict[str, Any] | None = None
    completed_report_hash: str | None = None
    if execution_journal is not None:
        journal_binding, completed_report_hash = _verify_execution_journal(
            execution_journal,
            campaign=campaign,
            campaign_hash=approved["campaign_hash"],
            phase_evidence=phase_evidence,
            artifact_root=artifact_root,
        )
    report_body = {
        **approved,
        "schema": "three-site-staging-full-matrix-report-v1",
        "status": "passed" if journal_binding is not None else "evidence_set_validated",
        "authoritative_controller_journal": journal_binding is not None,
        "execution_journal": journal_binding,
        "bound_artifacts": bindings,
        "phase_results": ordered,
        "phase_evidence_count": len(ordered),
        "scenario_execution_count": campaign["repetitions"]
        * sum(len(items) for items in PHASE_SCENARIOS.values()),
        "skip_count": 0,
        "cleanup_residue_count": 0,
        "production_touched": False,
    }
    report_hash = hashlib.sha256(canonical_json_bytes(report_body)).hexdigest()
    if completed_report_hash is not None and completed_report_hash != report_hash:
        raise FullMatrixCampaignError(
            "Full Matrix completed journal is not bound to this final report"
        )
    return {
        **report_body,
        "report_hash": report_hash,
    }


def _verify_execution_journal(
    path: Path,
    *,
    campaign: dict[str, Any],
    campaign_hash: str,
    phase_evidence: list[dict[str, Any]],
    artifact_root: Path,
) -> tuple[dict[str, Any], str | None]:
    records = verify_hash_chained_jsonl(path, label="Full Matrix execution journal")
    if not records or records[0].get("event") != "campaign_started":
        raise FullMatrixCampaignError("Full Matrix execution journal has no start")
    allowed_events = {
        "campaign_started", "scenario_started", "scenario_recovered",
        "scenario_passed", "phase_passed", "campaign_finalized",
        "campaign_completed", "campaign_blocked", "operation_started",
        "operation_passed",
    }
    identity = {
        "campaign_id": campaign["campaign_id"],
        "campaign_hash": campaign_hash,
        "release_sha": campaign["release_sha"],
        "activation_sha": campaign["activation_sha"],
    }
    campaign_start = _utc(campaign["generated_at"], label="campaign generated_at")
    campaign_end = _utc(campaign["expires_at"], label="campaign expires_at")
    operation_artifacts: dict[str, dict[str, Any]] = {}
    operation_raw_artifacts: set[str] = set()
    operation_starts: dict[str, tuple[str, dict[str, Any]]] = {}
    operation_results: dict[str, dict[str, Any]] = {}

    def retain_operation(
        result: Any,
        *,
        label: str,
        operation_kind: str,
        operation_id: str,
        operation_context: dict[str, Any],
    ) -> None:
        if not isinstance(result, dict):
            raise FullMatrixCampaignError(f"Full Matrix {label} result is missing")
        relative = _relative_artifact(result.get("artifact_path"))
        digest = str(result.get("artifact_sha256") or "")
        size = result.get("artifact_size")
        if (
            SHA256.fullmatch(digest) is None
            or type(size) is not int
            or size <= 0
            or result.get("evidence_hash") != digest
        ):
            raise FullMatrixCampaignError(f"Full Matrix {label} artifact record is invalid")
        try:
            measured_digest, measured_size = sha256_secure_file(
                artifact_root / relative,
                label=f"Full Matrix retained {label} artifact",
            )
        except Exception as exc:
            raise FullMatrixCampaignError(
                f"Full Matrix retained {label} artifact is missing or unsafe"
            ) from exc
        if measured_digest != digest or measured_size != size:
            raise FullMatrixCampaignError(f"Full Matrix retained {label} artifact differs")
        typed = secure_json(
            artifact_root / relative,
            label=f"Full Matrix retained {label} typed evidence",
        )
        verified = verify_operation_evidence(
            typed,
            campaign=campaign,
            campaign_hash=campaign_hash,
            operation_kind=operation_kind,
            operation_id=operation_id,
            operation_context=operation_context,
            artifact_root=artifact_root,
        )
        if operation_raw_artifacts.intersection(verified["artifact_paths"]):
            raise FullMatrixCampaignError("Full Matrix operation raw evidence was reused")
        operation_raw_artifacts.update(verified["artifact_paths"])
        record = {"path": relative, "sha256": digest, "size": size, "operation": label}
        previous = operation_artifacts.get(relative)
        if previous is not None and previous != record:
            raise FullMatrixCampaignError("Full Matrix operation artifact identity was reused")
        operation_artifacts[relative] = record

    for record in records:
        if (
            record.get("schema") != "three-site-staging-full-matrix-journal-v1"
            or record.get("event") not in allowed_events
            or any(record.get(key) != value for key, value in identity.items())
        ):
            raise FullMatrixCampaignError(
                "Full Matrix execution journal identity/schema differs"
            )
        observed = _utc(record.get("timestamp"), label="journal timestamp")
        if not campaign_start <= observed <= campaign_end:
            raise FullMatrixCampaignError(
                "Full Matrix execution journal time is outside campaign window"
            )
        event = record.get("event")
        if event in {"campaign_started", "operation_started", "operation_passed"}:
            operation_id = str(record.get("operation_id") or "")
            operation_kind = str(record.get("operation_kind") or "")
            context = record.get("operation_context")
            if (
                not operation_id or not operation_kind or not isinstance(context, dict)
                or set(context) != {
                    "phase", "scenario_id", "iteration", "failed", "attempt"
                }
                or operation_kind not in {"preflight", "recovery", "cleanup", "finalize"}
                or not isinstance(context.get("phase"), str)
                or not isinstance(context.get("scenario_id"), str)
                or type(context.get("iteration")) is not int
                or type(context.get("attempt")) is not int
                or context.get("failed") not in {None, True, False}
                or (operation_kind in {"preflight", "finalize"} and context != {
                    "phase": "", "scenario_id": "", "iteration": 0,
                    "failed": None, "attempt": 0,
                })
                or (operation_kind == "cleanup" and (
                    context["phase"] not in PHASE_SCENARIOS
                    or context["scenario_id"] != ""
                    or not 1 <= context["iteration"] <= campaign["repetitions"]
                    or type(context["failed"]) is not bool
                    or context["attempt"] != 0
                ))
                or (operation_kind == "recovery" and (
                    context["phase"] not in PHASE_SCENARIOS
                    or context["scenario_id"] not in PHASE_SCENARIOS.get(context["phase"], ())
                    or not 1 <= context["iteration"] <= campaign["repetitions"]
                    or context["failed"] is not None
                    or context["attempt"] < 1
                ))
                or operation_id != _matrix_operation_id(
                    campaign_hash, operation_kind,
                    phase=str(context["phase"]),
                    scenario_id=str(context["scenario_id"]),
                    iteration=int(context["iteration"]),
                    failed=context["failed"],
                    attempt=int(context["attempt"]),
                )
            ):
                raise FullMatrixCampaignError("Full Matrix operation intent identity is invalid")
            if event in {"campaign_started", "operation_started"}:
                if operation_id in operation_starts:
                    raise FullMatrixCampaignError("Full Matrix operation intent is duplicated")
                if event == "campaign_started" and operation_kind != "preflight":
                    raise FullMatrixCampaignError("Full Matrix start is not a preflight intent")
                operation_starts[operation_id] = (operation_kind, context)
            else:
                result = record.get("result")
                if (
                    operation_id not in operation_starts
                    or operation_id in operation_results
                    or operation_starts[operation_id] != (operation_kind, context)
                    or not isinstance(result, dict)
                    or result.get("operation_id") != operation_id
                ):
                    raise FullMatrixCampaignError("Full Matrix operation completion is invalid")
                label = (
                    f"{operation_kind}:{context['iteration']}:{context['phase']}:"
                    f"{context['scenario_id']}:{context['failed']}:{context['attempt']}"
                )
                retain_operation(
                    result,
                    label=label,
                    operation_kind=operation_kind,
                    operation_id=operation_id,
                    operation_context=context,
                )
                operation_results[operation_id] = result
    if any(record.get("event") == "campaign_blocked" for record in records):
        raise FullMatrixCampaignError("Full Matrix execution journal is blocked")
    if sum(record.get("event") == "campaign_started" for record in records) != 1:
        raise FullMatrixCampaignError("Full Matrix execution journal repeats start")
    if set(operation_starts) != set(operation_results):
        raise FullMatrixCampaignError("Full Matrix completed journal has an unfinished operation")
    required_operations = {
        _matrix_operation_id(campaign_hash, "preflight"),
        _matrix_operation_id(campaign_hash, "finalize"),
        *{
            _matrix_operation_id(
                campaign_hash, "cleanup", phase=phase,
                iteration=iteration, failed=False,
            )
            for iteration in range(1, campaign["repetitions"] + 1)
            for phase in PHASES
        },
    }
    if not required_operations.issubset(operation_results):
        raise FullMatrixCampaignError("Full Matrix journal lacks required operation completions")

    expected_scenarios = {
        (iteration, phase, scenario)
        for iteration in range(1, campaign["repetitions"] + 1)
        for phase in PHASES
        for scenario in PHASE_SCENARIOS[phase]
    }
    expected_phases = {
        (iteration, phase)
        for iteration in range(1, campaign["repetitions"] + 1)
        for phase in PHASES
    }
    scenario_hashes: dict[tuple[int, str, str], str] = {}
    scenario_attempts: dict[tuple[int, str, str], int] = {}
    open_scenarios: dict[tuple[int, str, str], int] = {}
    phase_hashes: dict[tuple[int, str], str] = {}
    for record in records:
        event = record["event"]
        if event == "scenario_started":
            key = (record.get("iteration"), record.get("phase"), record.get("scenario_id"))
            attempt = record.get("attempt")
            expected_attempt = scenario_attempts.get(key, 0) + 1
            expected_operation_id = _matrix_operation_id(
                campaign_hash, "scenario", phase=str(key[1]),
                scenario_id=str(key[2]), iteration=int(key[0]),
                attempt=expected_attempt,
            ) if key in expected_scenarios else ""
            if (
                key not in expected_scenarios
                or key in open_scenarios
                or type(attempt) is not int
                or attempt != expected_attempt
                or record.get("operation_id") != expected_operation_id
            ):
                raise FullMatrixCampaignError(
                    "Full Matrix journal scenario attempt is invalid"
                )
            scenario_attempts[key] = attempt
            open_scenarios[key] = attempt
        elif event == "scenario_passed":
            key = (record.get("iteration"), record.get("phase"), record.get("scenario_id"))
            result = record.get("result")
            attempt = record.get("attempt")
            expected_operation_id = _matrix_operation_id(
                campaign_hash, "scenario", phase=str(key[1]),
                scenario_id=str(key[2]), iteration=int(key[0]),
                attempt=int(attempt),
            ) if key in expected_scenarios and type(attempt) is int else ""
            if (
                key not in expected_scenarios
                or key in scenario_hashes
                or open_scenarios.get(key) != attempt
                or not isinstance(result, dict)
                or SHA256.fullmatch(str(result.get("evidence_hash") or "")) is None
                or record.get("operation_id") != expected_operation_id
                or result.get("operation_id") != expected_operation_id
                or result.get("attempt") != attempt
            ):
                raise FullMatrixCampaignError(
                    "Full Matrix journal scenario completion is invalid"
                )
            del open_scenarios[key]
            scenario_hashes[key] = result["evidence_hash"]
        elif event == "phase_passed":
            key = (record.get("iteration"), record.get("phase"))
            value = str(record.get("evidence_hash") or "")
            if key not in expected_phases or key in phase_hashes or SHA256.fullmatch(value) is None:
                raise FullMatrixCampaignError(
                    "Full Matrix journal phase completion is invalid"
                )
            cleanup_id = _matrix_operation_id(
                campaign_hash, "cleanup", phase=str(key[1]),
                iteration=int(key[0]), failed=False,
            )
            if record.get("cleanup_result") != operation_results.get(cleanup_id):
                raise FullMatrixCampaignError(
                    "Full Matrix phase completion differs from cleanup operation"
                )
            phase_hashes[key] = value
        elif event == "scenario_recovered":
            key = (record.get("iteration"), record.get("phase"), record.get("scenario_id"))
            attempt = record.get("attempt")
            recovery_id = _matrix_operation_id(
                campaign_hash, "recovery", phase=str(key[1]),
                scenario_id=str(key[2]), iteration=int(key[0]),
                attempt=int(attempt),
            ) if key in expected_scenarios and type(attempt) is int else ""
            if (
                key not in expected_scenarios
                or open_scenarios.get(key) != attempt
                or record.get("operation_id") != recovery_id
                or record.get("result") != operation_results.get(recovery_id)
            ):
                raise FullMatrixCampaignError(
                    "Full Matrix recovery event differs from its operation completion"
                )
            del open_scenarios[key]
    if open_scenarios:
        raise FullMatrixCampaignError("Full Matrix journal has an interrupted scenario")
    if set(scenario_hashes) != expected_scenarios or set(phase_hashes) != expected_phases:
        raise FullMatrixCampaignError("Full Matrix execution journal is incomplete")

    for evidence in phase_evidence:
        phase_key = (evidence["iteration"], evidence["phase"])
        expected_phase_hash = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
        if phase_hashes.get(phase_key) != expected_phase_hash:
            raise FullMatrixCampaignError(
                "Full Matrix phase evidence differs from execution journal"
            )
        for result in evidence["scenario_results"]:
            key = (evidence["iteration"], evidence["phase"], result["scenario_id"])
            if scenario_hashes.get(key) != result["evidence_hash"]:
                raise FullMatrixCampaignError(
                    "Full Matrix scenario evidence differs from execution journal"
                )

    finalized = [record for record in records if record["event"] == "campaign_finalized"]
    completed = [record for record in records if record["event"] == "campaign_completed"]
    if len(finalized) != 1 or len(completed) > 1:
        raise FullMatrixCampaignError("Full Matrix journal finalization is invalid")
    finalization = finalized[0]
    finalization_operation = operation_results[
        _matrix_operation_id(campaign_hash, "finalize")
    ]
    if (
        SHA256.fullmatch(str(finalization.get("finalization_evidence_hash") or "")) is None
        or finalization.get("result") != finalization_operation
        or finalization.get("finalization_evidence_hash")
        != finalization_operation.get("evidence_hash")
    ):
        raise FullMatrixCampaignError("Full Matrix finalization evidence is invalid")
    completed_report_hash: str | None = None
    if completed:
        if records[-1] is not completed[0] or records[-2] is not finalization:
            raise FullMatrixCampaignError("Full Matrix completion order is invalid")
        completed_report_hash = str(completed[0].get("report_hash") or "")
        if SHA256.fullmatch(completed_report_hash) is None:
            raise FullMatrixCampaignError("Full Matrix completion report hash is invalid")
        journal_head = completed[0]["previous_hash"]
    else:
        if records[-1] is not finalization:
            raise FullMatrixCampaignError("Full Matrix finalization must be the journal tail")
        journal_head = finalization["event_hash"]
    return (
        {
            "schema": "three-site-staging-full-matrix-journal-binding-v1",
            "head_before_completion": journal_head,
            "finalization_evidence_hash": finalization["finalization_evidence_hash"],
            "scenario_completion_count": len(scenario_hashes),
            "phase_completion_count": len(phase_hashes),
            "operation_artifacts": [
                operation_artifacts[path] for path in sorted(operation_artifacts)
            ],
            "operation_artifact_count": len(operation_artifacts),
        },
        completed_report_hash,
    )
