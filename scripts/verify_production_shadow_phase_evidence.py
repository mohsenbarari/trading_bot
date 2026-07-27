#!/usr/bin/env python3
"""Semantically verify one precommit production-shadow phase evidence file.

The verifier accepts only an owner-only strict JSON document bound to one
campaign, operation, release, manifest, plan, approval, phase, and immutable
evidence contract.  Each phase has an exact role/host/transport set and an
exact claim set with typed expected values.  A digest is returned only after
the full document passes; the verifier never contacts production or mutates it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any
from uuid import UUID


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    sha256_secure_file,
)
from core.docker_image_identity import (  # noqa: E402
    DockerImageIdentityError,
    verify_content_descriptor,
)
from scripts.production_shadow_cutover_controller import (  # noqa: E402
    ARTIFACT_FIELDS,
    DOCKER_RUNTIME_ROLES,
    EXPECTED_TOPOLOGY,
    IMAGE_ARTIFACT_FIELDS,
    IMAGE_KINDS,
    PHASES,
    PHASE_SPECS,
    PRECOMMIT_JOURNAL_STATUS,
    ProductionCutoverJournal,
    read_root_only_manifest,
    render_plan,
)


EVIDENCE_SCHEMA = "production-shadow-phase-evidence-v1"
VERIFICATION_SCHEMA = "production-shadow-phase-evidence-verification-v1"
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
MAX_EVIDENCE_AGE = timedelta(hours=2)
MAX_FUTURE_SKEW = timedelta(seconds=60)
MAX_ROLE_CAPTURE_SKEW = timedelta(minutes=30)
PHASE_MAX_AGE = {
    "witness_lease": timedelta(minutes=5),
    "convergence_gate": timedelta(minutes=15),
    "readonly_upstream_switch": timedelta(minutes=5),
    "precommit_no_due_mutator_delta": timedelta(minutes=10),
    "precommit_provider_free_queue_rehydrate": timedelta(minutes=10),
    "precommit_irreversible_effect_watchers": timedelta(minutes=10),
    "pre_first_write_acceptance": timedelta(minutes=5),
}

EVIDENCE_FIELDS = frozenset(
    {
        "schema",
        "phase_evidence_schema_sha256",
        "campaign_id",
        "operation_id",
        "release_sha",
        "legacy_release_sha",
        "manifest_sha256",
        "plan_sha256",
        "approval_sha256",
        "manifest_artifact_bindings",
        "phase",
        "operation",
        "journal_status",
        "status",
        "captured_at",
        "business_write_observed",
        "prior_phase_evidence",
        "prior_phase_evidence_closure_sha256",
        "prior_claim_bindings",
        "phase_input_closure_sha256",
        "role_attestations",
        "claims",
    }
)
VERIFICATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "phase",
        "operation",
        "campaign_id",
        "operation_id",
        "release_sha",
        "legacy_release_sha",
        "manifest_sha256",
        "plan_sha256",
        "approval_sha256",
        "phase_evidence_schema_sha256",
        "manifest_artifact_bindings_sha256",
        "prior_phase_evidence_closure_sha256",
        "phase_input_closure_sha256",
        "prior_phase_count",
        "evidence_sha256",
        "verified_roles",
        "verified_claim_count",
        "captured_at",
        "verified_at",
        "production_contacted",
    }
)
ROLE_ATTESTATION_FIELDS = frozenset(
    {
        "role",
        "expected_host",
        "operation",
        "request_sha256",
        "app_release_sha",
        "agent_artifact_sha256",
        "host_identity_observed",
        "observed_at",
        "status",
        "transport",
        "source_artifact_sha256",
    }
)
CLAIM_FIELDS = frozenset({"value", "source_sha256"})
PRIOR_PHASE_EVIDENCE_FIELDS = frozenset({"phase", "evidence_sha256"})
PRIOR_CLAIM_BINDING_FIELDS = frozenset(
    {
        "target_claim",
        "source_phase",
        "source_claim",
        "source_evidence_sha256",
        "value",
    }
)
HOST_AGENT_VALIDATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "request_sha256",
        "operation",
        "role",
        "campaign_id",
        "operation_id",
        "app_release_sha",
        "manifest_sha256",
        "approval_sha256",
        "expected_host",
        "observed_host",
        "required_journal_status",
        "business_write_policy",
        "agent_artifact_sha256",
        "host_agent_contract_sha256",
        "transport",
        "observed_at",
        "host_identity_observed",
        "execution_supported",
        "production_contacted",
    }
)
CLAIM_SOURCE_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "phase",
        "operation",
        "claim",
        "value",
        "observed_at",
        "status",
    }
)


class PhaseEvidenceError(RuntimeError):
    """Raised when phase evidence does not prove the exact contract."""


@dataclass(frozen=True)
class ClaimRule:
    kind: str
    expected: Any = None


def exact(value: Any) -> ClaimRule:
    return ClaimRule("exact", value)


HASH = ClaimRule("nonzero-sha256")
POSITIVE_INT = ClaimRule("positive-int")
IMAGE_ID = ClaimRule("immutable-image-id")
NONEMPTY_STRING = ClaimRule("nonempty-string")


PHASE_CLAIM_RULES: dict[str, dict[str, ClaimRule]] = {
    "pre_freeze_evidence": {
        "release_bundle_sha256": HASH,
        "bot_fi_role_material_sha256": HASH,
        "webapp_fi_role_material_sha256": HASH,
        "webapp_ir_role_material_sha256": HASH,
        "witness_role_material_sha256": HASH,
        "shadow_compose_sha256": HASH,
        "app_image_config_digest": IMAGE_ID,
        "postgres_image_config_digest": IMAGE_ID,
        "redis_image_config_digest": IMAGE_ID,
        "nginx_image_config_digest": IMAGE_ID,
        "app_image_content_identity": IMAGE_ID,
        "postgres_image_content_identity": IMAGE_ID,
        "redis_image_content_identity": IMAGE_ID,
        "nginx_image_content_identity": IMAGE_ID,
        "bot_fi_app_runtime_image_id": IMAGE_ID,
        "bot_fi_postgres_runtime_image_id": IMAGE_ID,
        "bot_fi_redis_runtime_image_id": IMAGE_ID,
        "bot_fi_nginx_runtime_image_id": IMAGE_ID,
        "webapp_fi_app_runtime_image_id": IMAGE_ID,
        "webapp_fi_postgres_runtime_image_id": IMAGE_ID,
        "webapp_fi_redis_runtime_image_id": IMAGE_ID,
        "webapp_fi_nginx_runtime_image_id": IMAGE_ID,
        "webapp_ir_app_runtime_image_id": IMAGE_ID,
        "webapp_ir_postgres_runtime_image_id": IMAGE_ID,
        "webapp_ir_redis_runtime_image_id": IMAGE_ID,
        "webapp_ir_nginx_runtime_image_id": IMAGE_ID,
        "postgres_image_ref": NONEMPTY_STRING,
        "legacy_bot_rollback_sha256": HASH,
        "legacy_webapp_rollback_sha256": HASH,
        "legacy_bot_redis_rollback_sha256": HASH,
        "legacy_webapp_redis_rollback_sha256": HASH,
        "nginx_rollback_generation_sha256": HASH,
        "host_agent_sha256": HASH,
        "host_agent_contract_sha256": HASH,
        "exact_release_image_compose_attested": exact(True),
        "canonical_host_identity_attested": exact(True),
        "legacy_rollback_artifact_set_attested": exact(True),
        "active_route_generation_set_sha256": HASH,
    },
    "shadow_startup_normalization": {
        "legacy_resource_delta_count": exact(0),
        "operation_owned_running_container_count": exact(0),
        "unplanned_container_delta_count": exact(0),
    },
    "freeze_generation_install": {
        "manifest_freeze_generation_sha256": HASH,
        "staged_generation_set_sha256": HASH,
        "previous_generation_set_sha256": HASH,
        "active_generation_unchanged": exact(True),
        "staged_vhost_count": exact(3),
    },
    "freeze_generation_test": {
        "manifest_freeze_generation_sha256": HASH,
        "nginx_test_failure_count": exact(0),
        "tested_vhost_count": exact(3),
        "active_generation_unchanged": exact(True),
    },
    "freeze_generation_activate": {
        "manifest_freeze_generation_sha256": HASH,
        "write_blocked_vhost_count": exact(3),
        "per_host_generation_readback_verified": exact(True),
        "compensating_restore_ready": exact(True),
    },
    "stop_legacy_writers": {
        "legacy_writer_process_count": exact(0),
        "legacy_writer_database_client_count": exact(0),
        "legacy_file_mutator_process_count": exact(0),
    },
    "zero_writer_surface_readback": {
        "write_capable_route_count": exact(0),
        "legacy_writer_process_count": exact(0),
        "writer_database_client_count": exact(0),
        "file_mutator_process_count": exact(0),
        "externally_read_vhost_count": exact(3),
    },
    "final_snapshot_hashes": {
        "postgres_snapshot_set_sha256": HASH,
        "reviewed_file_snapshot_set_sha256": HASH,
        "legacy_redis_sealed_set_sha256": HASH,
        "legacy_redis_restore_member_count": exact(0),
        "frozen_writer_delta_count": exact(0),
        "file_mutator_process_count": exact(0),
        "file_snapshot_pre_post_stat_stable": exact(True),
        "file_snapshot_tree_hash_stable": exact(True),
    },
    "pristine_shadow_redis": {
        "redis_target_count": POSITIVE_INT,
        "unsafe_redis_path_count": exact(0),
        "nonempty_redis_target_count": exact(0),
        "legacy_redis_restore_byte_count": exact(0),
    },
    "shadow_restore": {
        "postgres_restore_verified": exact(True),
        "reviewed_file_restore_verified": exact(True),
        "legacy_redis_restore_byte_count": exact(0),
        "non_operation_resource_delta_count": exact(0),
        "restored_postgres_snapshot_set_sha256": HASH,
        "restored_reviewed_file_snapshot_set_sha256": HASH,
        "restore_result_set_sha256": HASH,
    },
    "shadow_roles_pre_migration": {
        "least_privilege_role_set_verified": exact(True),
        "excessive_grant_count": exact(0),
    },
    "shadow_migrate": {
        "restore_result_set_sha256": HASH,
        "alembic_chain_state": exact("target"),
        "off_chain_revision_count": exact(0),
        "invalid_unready_index_count": exact(0),
        "schema_fingerprint_sha256": HASH,
        "migration_journal_sha256": HASH,
    },
    "shadow_roles_post_migration": {
        "least_privilege_role_set_verified": exact(True),
        "excessive_grant_count": exact(0),
        "post_migration_grant_set_sha256": HASH,
        "migrated_schema_fingerprint_sha256": HASH,
    },
    "shadow_fence": {
        "fenced_database_count": POSITIVE_INT,
        "unfenced_writer_count": exact(0),
        "database_event_fence_verified": exact(True),
        "migrated_schema_fingerprint_sha256": HASH,
        "fence_configuration_sha256": HASH,
    },
    "witness_lease": {
        "witness_signature_verified": exact(True),
        "singleton_live_lease_count": exact(1),
        "lease_epoch": POSITIVE_INT,
        "lease_readback_sha256": HASH,
    },
    "convergence_gate": {
        "schema_role_fence_verified": exact(True),
        "queue_state_verified": exact(True),
        "database_business_drift_count": exact(0),
        "dr_unapplied_event_count": exact(0),
        "dr_tls_peer_handshakes_verified": exact(True),
        "blob_keyring_roundtrip_verified": exact(True),
        "destination_firewall_allowlists_verified": exact(True),
        "signed_witness_attestation_verified": exact(True),
        "migrated_schema_fingerprint_sha256": HASH,
        "fence_configuration_sha256": HASH,
        "convergence_state_sha256": HASH,
    },
    "readonly_upstream_switch": {
        "readonly_shadow_vhost_count": exact(3),
        "write_blocked_vhost_count": exact(3),
        "per_host_generation_readback_verified": exact(True),
        "iran_public_route_count": exact(0),
        "iran_effect_owner_count": exact(0),
    },
    "precommit_no_due_mutator_delta": {
        "running_business_mutator_count": exact(0),
        "due_otp_job_count": exact(0),
        "inflight_effect_count": exact(0),
        "telegram_lease_count": exact(0),
        "provider_attempt_delta_count": exact(0),
        "authoritative_sequence_baseline_sha256": HASH,
    },
    "precommit_provider_free_queue_rehydrate": {
        "bot_token_present": exact(False),
        "provider_egress_attempt_count": exact(0),
        "queue_claim_delta_count": exact(0),
        "bot_polling_started": exact(False),
        "source_row_delta_count": exact(0),
        "source_lease_delta_count": exact(0),
        "source_dispatch_delta_count": exact(0),
        "queue_rehydration_verified": exact(True),
        "rehydrated_queue_state_sha256": HASH,
    },
    "precommit_irreversible_effect_watchers": {
        "telegram_dispatch_watcher_armed": exact(True),
        "dr_effect_watcher_armed": exact(True),
        "inline_sms_watcher_armed": exact(True),
        "authoritative_sequence_watcher_armed": exact(True),
        "watcher_baseline_set_sha256": HASH,
    },
    "pre_first_write_acceptance": {
        "acceptance_gate_failure_count": exact(0),
        "rollback_rehearsal_verified": exact(True),
        "business_write_count": exact(0),
        "public_route_generation_set_sha256": HASH,
        "witness_lease_readback_verified": exact(True),
        "live_writer_process_count": exact(0),
        "live_write_capable_route_count": exact(0),
        "live_writer_database_client_count": exact(0),
        "live_schema_role_fence_verified": exact(True),
        "live_queue_state_verified": exact(True),
        "live_dr_unapplied_event_count": exact(0),
        "live_dr_tls_verified": exact(True),
        "live_blob_roundtrip_verified": exact(True),
        "live_firewall_allowlists_verified": exact(True),
        "live_watchers_armed": exact(True),
        "live_readback_set_sha256": HASH,
        "convergence_state_sha256": HASH,
        "rehydrated_queue_state_sha256": HASH,
        "watcher_baseline_set_sha256": HASH,
    },
}

PHASE_MANIFEST_CLAIM_BINDINGS = {
    "pre_freeze_evidence": {
        "release_bundle_sha256": "release_bundle_sha256",
        "bot_fi_role_material_sha256": "role_materials.bot_fi.sha256",
        "webapp_fi_role_material_sha256": "role_materials.webapp_fi.sha256",
        "webapp_ir_role_material_sha256": "role_materials.webapp_ir.sha256",
        "witness_role_material_sha256": "role_materials.witness.sha256",
        "shadow_compose_sha256": "shadow_compose_sha256",
        "app_image_config_digest": "image_artifacts.app.config_digest",
        "postgres_image_config_digest": (
            "image_artifacts.postgres.config_digest"
        ),
        "redis_image_config_digest": "image_artifacts.redis.config_digest",
        "nginx_image_config_digest": "image_artifacts.nginx.config_digest",
        "app_image_content_identity": "image_artifacts.app.content_identity",
        "postgres_image_content_identity": (
            "image_artifacts.postgres.content_identity"
        ),
        "redis_image_content_identity": (
            "image_artifacts.redis.content_identity"
        ),
        "nginx_image_content_identity": (
            "image_artifacts.nginx.content_identity"
        ),
        "bot_fi_app_runtime_image_id": (
            "role_runtime_image_ids.bot_fi.app"
        ),
        "bot_fi_postgres_runtime_image_id": (
            "role_runtime_image_ids.bot_fi.postgres"
        ),
        "bot_fi_redis_runtime_image_id": (
            "role_runtime_image_ids.bot_fi.redis"
        ),
        "bot_fi_nginx_runtime_image_id": (
            "role_runtime_image_ids.bot_fi.nginx"
        ),
        "webapp_fi_app_runtime_image_id": (
            "role_runtime_image_ids.webapp_fi.app"
        ),
        "webapp_fi_postgres_runtime_image_id": (
            "role_runtime_image_ids.webapp_fi.postgres"
        ),
        "webapp_fi_redis_runtime_image_id": (
            "role_runtime_image_ids.webapp_fi.redis"
        ),
        "webapp_fi_nginx_runtime_image_id": (
            "role_runtime_image_ids.webapp_fi.nginx"
        ),
        "webapp_ir_app_runtime_image_id": (
            "role_runtime_image_ids.webapp_ir.app"
        ),
        "webapp_ir_postgres_runtime_image_id": (
            "role_runtime_image_ids.webapp_ir.postgres"
        ),
        "webapp_ir_redis_runtime_image_id": (
            "role_runtime_image_ids.webapp_ir.redis"
        ),
        "webapp_ir_nginx_runtime_image_id": (
            "role_runtime_image_ids.webapp_ir.nginx"
        ),
        "postgres_image_ref": "postgres_image_ref",
        "legacy_bot_rollback_sha256": "legacy_bot_rollback_sha256",
        "legacy_webapp_rollback_sha256": "legacy_webapp_rollback_sha256",
        "legacy_bot_redis_rollback_sha256": (
            "legacy_bot_redis_rollback_sha256"
        ),
        "legacy_webapp_redis_rollback_sha256": (
            "legacy_webapp_redis_rollback_sha256"
        ),
        "nginx_rollback_generation_sha256": (
            "nginx_rollback_generation_sha256"
        ),
        "host_agent_sha256": "host_agent_sha256",
        "host_agent_contract_sha256": "host_agent_contract_sha256",
    },
    "freeze_generation_install": {
        "manifest_freeze_generation_sha256": "nginx_freeze_generation_sha256",
    },
    "freeze_generation_test": {
        "manifest_freeze_generation_sha256": "nginx_freeze_generation_sha256",
    },
    "freeze_generation_activate": {
        "manifest_freeze_generation_sha256": "nginx_freeze_generation_sha256",
    },
}

PHASE_PRIOR_CLAIM_BINDINGS = {
    "shadow_restore": {
        "restored_postgres_snapshot_set_sha256": (
            "final_snapshot_hashes",
            "postgres_snapshot_set_sha256",
        ),
        "restored_reviewed_file_snapshot_set_sha256": (
            "final_snapshot_hashes",
            "reviewed_file_snapshot_set_sha256",
        ),
    },
    "shadow_migrate": {
        "restore_result_set_sha256": (
            "shadow_restore",
            "restore_result_set_sha256",
        ),
    },
    "shadow_roles_post_migration": {
        "migrated_schema_fingerprint_sha256": (
            "shadow_migrate",
            "schema_fingerprint_sha256",
        ),
    },
    "shadow_fence": {
        "migrated_schema_fingerprint_sha256": (
            "shadow_migrate",
            "schema_fingerprint_sha256",
        ),
    },
    "convergence_gate": {
        "migrated_schema_fingerprint_sha256": (
            "shadow_migrate",
            "schema_fingerprint_sha256",
        ),
        "fence_configuration_sha256": (
            "shadow_fence",
            "fence_configuration_sha256",
        ),
    },
    "pre_first_write_acceptance": {
        "convergence_state_sha256": (
            "convergence_gate",
            "convergence_state_sha256",
        ),
        "rehydrated_queue_state_sha256": (
            "precommit_provider_free_queue_rehydrate",
            "rehydrated_queue_state_sha256",
        ),
        "watcher_baseline_set_sha256": (
            "precommit_irreversible_effect_watchers",
            "watcher_baseline_set_sha256",
        ),
    },
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _contract_document() -> dict[str, Any]:
    return {
        "schema": "production-shadow-phase-evidence-contract-v1",
        "evidence_schema": EVIDENCE_SCHEMA,
        "evidence_fields": sorted(EVIDENCE_FIELDS),
        "verification_schema": VERIFICATION_SCHEMA,
        "verification_fields": sorted(VERIFICATION_FIELDS),
        "role_attestation_fields": sorted(ROLE_ATTESTATION_FIELDS),
        "claim_fields": sorted(CLAIM_FIELDS),
        "prior_phase_evidence_fields": sorted(PRIOR_PHASE_EVIDENCE_FIELDS),
        "prior_claim_binding_fields": sorted(PRIOR_CLAIM_BINDING_FIELDS),
        "manifest_artifact_fields": sorted(ARTIFACT_FIELDS),
        "journal_status": PRECOMMIT_JOURNAL_STATUS,
        "max_evidence_age_seconds": int(MAX_EVIDENCE_AGE.total_seconds()),
        "max_future_skew_seconds": int(MAX_FUTURE_SKEW.total_seconds()),
        "max_role_capture_skew_seconds": int(MAX_ROLE_CAPTURE_SKEW.total_seconds()),
        "phases": [
            {
                "phase": spec.phase,
                "operation": spec.operation,
                "roles": list(spec.roles),
                "max_age_seconds": int(
                    PHASE_MAX_AGE.get(
                        spec.phase,
                        MAX_EVIDENCE_AGE,
                    ).total_seconds()
                ),
                "manifest_claim_bindings": PHASE_MANIFEST_CLAIM_BINDINGS.get(
                    spec.phase,
                    {},
                ),
                "prior_claim_bindings": {
                    target: {
                        "source_phase": source[0],
                        "source_claim": source[1],
                    }
                    for target, source in PHASE_PRIOR_CLAIM_BINDINGS.get(
                        spec.phase,
                        {},
                    ).items()
                },
                "claims": {
                    name: {"kind": rule.kind, "expected": rule.expected}
                    for name, rule in sorted(PHASE_CLAIM_RULES[spec.phase].items())
                },
            }
            for spec in PHASE_SPECS
        ],
    }


PHASE_EVIDENCE_CONTRACT = _contract_document()
PHASE_EVIDENCE_CONTRACT_SHA256 = hashlib.sha256(
    _canonical_json(PHASE_EVIDENCE_CONTRACT)
).hexdigest()
PHASE_SPEC_BY_NAME = {spec.phase: spec for spec in PHASE_SPECS}


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PhaseEvidenceError(f"duplicate evidence field: {key}")
        result[key] = value
    return result


def _canonical_uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise PhaseEvidenceError(f"{label} is not a canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise PhaseEvidenceError(f"{label} is not a canonical UUID") from exc
    if str(parsed) != value or parsed.version not in {1, 2, 3, 4, 5}:
        raise PhaseEvidenceError(f"{label} is not a canonical UUID")
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == "0" * 64
    ):
        raise PhaseEvidenceError(f"{label} is not a nonzero SHA-256")
    return value


def _validate_manifest_artifacts(
    value: Any,
    *,
    release_sha: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(ARTIFACT_FIELDS):
        raise PhaseEvidenceError("manifest artifact binding fields are not exact")
    hash_fields = {
        "release_bundle_sha256",
        "legacy_bot_rollback_sha256",
        "legacy_webapp_rollback_sha256",
        "legacy_bot_redis_rollback_sha256",
        "legacy_webapp_redis_rollback_sha256",
        "shadow_compose_sha256",
        "cutover_approval_sha256",
        "nginx_freeze_generation_sha256",
        "nginx_rollback_generation_sha256",
        "postcommit_executor_contract_sha256",
        "phase_evidence_schema_sha256",
        "host_agent_sha256",
        "host_agent_contract_sha256",
        "phase_evidence_verifier_sha256",
    }
    for field in hash_fields:
        _nonzero_sha256(value[field], label=f"manifest artifact {field}")
    for field in (
        "release_bundle_bytes",
    ):
        observed = value[field]
        if (
            isinstance(observed, bool)
            or not isinstance(observed, int)
            or not 1 <= observed <= 64 * 1024 * 1024 * 1024
        ):
            raise PhaseEvidenceError(
                f"manifest artifact {field} is outside its size bound"
            )
    role_materials = value["role_materials"]
    if (
        not isinstance(role_materials, dict)
        or set(role_materials) != set(EXPECTED_TOPOLOGY)
    ):
        raise PhaseEvidenceError("manifest role material roles are not exact")
    role_digests: set[str] = set()
    for role, topology in EXPECTED_TOPOLOGY.items():
        row = role_materials[role]
        if (
            not isinstance(row, dict)
            or set(row) != {"sha256", "bytes", "transport", "format"}
        ):
            raise PhaseEvidenceError(
                f"manifest role material {role} fields are not exact"
            )
        digest = _nonzero_sha256(
            row["sha256"],
            label=f"manifest role material {role}",
        )
        observed_bytes = row["bytes"]
        expected_format = (
            "production-shadow-witness-material-tar"
            if role == "witness"
            else "production-shadow-role-material-tar"
        )
        if (
            isinstance(observed_bytes, bool)
            or not isinstance(observed_bytes, int)
            or not 1 <= observed_bytes <= 64 * 1024 * 1024 * 1024
            or row["transport"] != topology["transport"]
            or row["format"] != expected_format
        ):
            raise PhaseEvidenceError(
                f"manifest role material {role} is invalid"
            )
        role_digests.add(digest)
    if len(role_digests) != len(EXPECTED_TOPOLOGY):
        raise PhaseEvidenceError(
            "manifest role material digests must be distinct"
        )

    image_artifacts = value["image_artifacts"]
    if (
        not isinstance(image_artifacts, dict)
        or set(image_artifacts) != set(IMAGE_KINDS)
    ):
        raise PhaseEvidenceError(
            "manifest image artifact inventory is not exact"
        )
    for kind in IMAGE_KINDS:
        row = image_artifacts[kind]
        if (
            not isinstance(row, dict)
            or set(row) != set(IMAGE_ARTIFACT_FIELDS)
        ):
            raise PhaseEvidenceError(
                f"manifest image artifact {kind} fields are not exact"
            )
        _nonzero_sha256(
            row["archive_sha256"],
            label=f"manifest image artifact {kind} archive",
        )
        _nonzero_sha256(
            row["content_identity"].removeprefix("sha256:")
            if isinstance(row["content_identity"], str)
            else row["content_identity"],
            label=f"manifest image artifact {kind} content identity",
        )
        if (
            isinstance(row["archive_bytes"], bool)
            or not isinstance(row["archive_bytes"], int)
            or not 1 <= row["archive_bytes"] <= 64 * 1024 * 1024 * 1024
            or not isinstance(row["config_digest"], str)
            or IMAGE_ID_RE.fullmatch(row["config_digest"]) is None
            or row["config_digest"] == "sha256:" + "0" * 64
            or not isinstance(row["content_identity"], str)
            or IMAGE_ID_RE.fullmatch(row["content_identity"]) is None
            or row["content_identity"] == "sha256:" + "0" * 64
        ):
            raise PhaseEvidenceError(
                f"manifest image artifact {kind} identity is invalid"
            )
        try:
            observed_identity = verify_content_descriptor(
                row["content_descriptor"]
            )
        except DockerImageIdentityError as exc:
            raise PhaseEvidenceError(
                f"manifest image artifact {kind} descriptor is invalid"
            ) from exc
        if (
            row["content_descriptor"]["architecture"] != "amd64"
            or row["content_descriptor"]["os"] != "linux"
            or observed_identity != row["content_identity"]
        ):
            raise PhaseEvidenceError(
                f"manifest image artifact {kind} content identity differs"
            )
    for field in ("archive_sha256", "config_digest", "content_identity"):
        if len(
            {image_artifacts[kind][field] for kind in IMAGE_KINDS}
        ) != len(IMAGE_KINDS):
            raise PhaseEvidenceError(
                f"manifest image {field} values must be distinct"
            )

    runtime_ids = value["role_runtime_image_ids"]
    if (
        not isinstance(runtime_ids, dict)
        or set(runtime_ids) != set(DOCKER_RUNTIME_ROLES)
    ):
        raise PhaseEvidenceError(
            "manifest runtime image roles are not exact"
        )
    for role in DOCKER_RUNTIME_ROLES:
        role_ids = runtime_ids[role]
        if (
            not isinstance(role_ids, dict)
            or set(role_ids) != set(IMAGE_KINDS)
            or any(
                not isinstance(observed, str)
                or IMAGE_ID_RE.fullmatch(observed) is None
                or observed == "sha256:" + "0" * 64
                for observed in role_ids.values()
            )
            or len(set(role_ids.values())) != len(IMAGE_KINDS)
        ):
            raise PhaseEvidenceError(
                f"manifest runtime image inventory for {role} is invalid"
            )
    if (
        value["postgres_runtime_uid"] != 70
        or value["postgres_runtime_gid"] != 70
    ):
        raise PhaseEvidenceError(
            "manifest PostgreSQL runtime UID/GID is invalid"
        )
    if value["postgres_image_ref"] != (
        f"trading_bot_postgres_boottime:15-{release_sha}"
    ):
        raise PhaseEvidenceError("manifest PostgreSQL image ref is invalid")
    return dict(value)


def _manifest_artifact_binding_value(
    artifacts: dict[str, Any],
    binding: str,
) -> Any:
    current: Any = artifacts
    for component in binding.split("."):
        if not isinstance(current, dict) or component not in current:
            raise PhaseEvidenceError(
                f"manifest artifact binding {binding} is invalid"
            )
        current = current[component]
    return current


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise PhaseEvidenceError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PhaseEvidenceError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PhaseEvidenceError(f"{label} timestamp lacks a timezone")
    return parsed.astimezone(timezone.utc)


def _typed_exact(actual: Any, expected: Any) -> bool:
    return type(actual) is type(expected) and actual == expected


def _validate_claim(name: str, value: Any, rule: ClaimRule) -> None:
    if not isinstance(value, dict) or set(value) != CLAIM_FIELDS:
        raise PhaseEvidenceError(f"claim {name} fields are not exact")
    _nonzero_sha256(value["source_sha256"], label=f"claim {name} source")
    actual = value["value"]
    if rule.kind == "exact":
        if not _typed_exact(actual, rule.expected):
            raise PhaseEvidenceError(f"claim {name} does not equal its required value")
    elif rule.kind == "nonzero-sha256":
        _nonzero_sha256(actual, label=f"claim {name} value")
    elif rule.kind == "positive-int":
        if type(actual) is not int or actual < 1:
            raise PhaseEvidenceError(f"claim {name} must be a positive integer")
    elif rule.kind == "immutable-image-id":
        if (
            not isinstance(actual, str)
            or IMAGE_ID_RE.fullmatch(actual) is None
            or actual == "sha256:" + "0" * 64
        ):
            raise PhaseEvidenceError(f"claim {name} must be an immutable image ID")
    elif rule.kind == "nonempty-string":
        if not isinstance(actual, str) or not actual:
            raise PhaseEvidenceError(f"claim {name} must be a nonempty string")
    else:
        raise PhaseEvidenceError(f"claim {name} has an unknown contract rule")


def _dynamic_claim_names(phase: str) -> set[str]:
    return {
        name
        for name, rule in PHASE_CLAIM_RULES[phase].items()
        if rule.kind != "exact"
    }


def _validate_expected_dynamic_claims(
    value: Any,
    *,
    phase: str,
) -> dict[str, Any]:
    expected_names = _dynamic_claim_names(phase)
    if not isinstance(value, dict) or set(value) != expected_names:
        raise PhaseEvidenceError("expected dynamic phase claim mapping is not exact")
    for name in expected_names:
        _validate_claim(
            name,
            {
                "value": value[name],
                "source_sha256": "1" * 64,
            },
            PHASE_CLAIM_RULES[phase][name],
        )
    return dict(value)


def _expected_prior_phase_rows(
    *,
    phase: str,
    expected_digests: Any,
) -> list[dict[str, str]]:
    phase_index = PHASES.index(phase)
    expected_phases = PHASES[:phase_index]
    if (
        not isinstance(expected_digests, dict)
        or set(expected_digests) != set(expected_phases)
    ):
        raise PhaseEvidenceError("expected prior phase evidence mapping is not exact")
    rows: list[dict[str, str]] = []
    for prior_phase in expected_phases:
        digest = _nonzero_sha256(
            expected_digests[prior_phase],
            label=f"prior phase {prior_phase}",
        )
        rows.append({"phase": prior_phase, "evidence_sha256": digest})
    return rows


def _derive_prior_claim_rows(
    *,
    phase: str,
    prior_digests: dict[str, str],
    prior_records: Any,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    legacy_release_sha: str,
    manifest_sha256: str,
    plan_sha256: str,
) -> list[dict[str, Any]]:
    bindings = PHASE_PRIOR_CLAIM_BINDINGS.get(phase, {})
    expected_prior_phases = set(PHASES[: PHASES.index(phase)])
    if not isinstance(prior_records, dict) or set(prior_records) != expected_prior_phases:
        raise PhaseEvidenceError("prior phase evidence record mapping is not exact")
    validated_documents: dict[str, dict[str, Any]] = {}
    for prior_phase in expected_prior_phases:
        record = prior_records[prior_phase]
        if (
            not isinstance(record, dict)
            or set(record) != {"document", "file_sha256"}
            or record["file_sha256"] != prior_digests[prior_phase]
            or not isinstance(record["document"], dict)
            or set(record["document"]) != EVIDENCE_FIELDS
        ):
            raise PhaseEvidenceError(
                f"prior phase {prior_phase} record does not match the journal digest"
            )
        prior_document = record["document"]
        expected_bindings = {
            "schema": EVIDENCE_SCHEMA,
            "phase_evidence_schema_sha256": PHASE_EVIDENCE_CONTRACT_SHA256,
            "campaign_id": campaign_id,
            "operation_id": operation_id,
            "release_sha": release_sha,
            "legacy_release_sha": legacy_release_sha,
            "manifest_sha256": manifest_sha256,
            "plan_sha256": plan_sha256,
            "phase": prior_phase,
            "operation": PHASE_SPEC_BY_NAME[prior_phase].operation,
            "journal_status": PRECOMMIT_JOURNAL_STATUS,
            "status": "passed",
            "business_write_observed": False,
        }
        if any(
            not _typed_exact(prior_document[field], expected)
            for field, expected in expected_bindings.items()
        ):
            raise PhaseEvidenceError(
                f"prior phase {prior_phase} record identity is invalid"
            )
        if not isinstance(prior_document["claims"], dict):
            raise PhaseEvidenceError(
                f"prior phase {prior_phase} claims are unavailable"
            )
        validated_documents[prior_phase] = prior_document

    current_index = PHASES.index(phase)
    rows: list[dict[str, Any]] = []
    for target_claim in sorted(bindings):
        source_phase, source_claim = bindings[target_claim]
        if (
            source_phase not in prior_digests
            or PHASES.index(source_phase) >= current_index
            or source_claim not in PHASE_CLAIM_RULES[source_phase]
        ):
            raise PhaseEvidenceError("prior claim binding contract is invalid")
        source_claims = validated_documents[source_phase]["claims"]
        if source_claim not in source_claims:
            raise PhaseEvidenceError(
                f"prior phase {source_phase} lacks source claim {source_claim}"
            )
        source_value = source_claims[source_claim]
        if (
            not isinstance(source_value, dict)
            or set(source_value) != CLAIM_FIELDS
        ):
            raise PhaseEvidenceError(
                f"prior phase {source_phase} source claim is invalid"
            )
        _validate_claim(
            source_claim,
            source_value,
            PHASE_CLAIM_RULES[source_phase][source_claim],
        )
        value = source_value["value"]
        _validate_claim(
            target_claim,
            {"value": value, "source_sha256": "1" * 64},
            PHASE_CLAIM_RULES[phase][target_claim],
        )
        rows.append(
            {
                "target_claim": target_claim,
                "source_phase": source_phase,
                "source_claim": source_claim,
                "source_evidence_sha256": prior_digests[source_phase],
                "value": value,
            }
        )
    return rows


def verify_phase_evidence(
    document: Any,
    *,
    expected_phase: str,
    expected_campaign_id: str,
    expected_operation_id: str,
    expected_release_sha: str,
    expected_legacy_release_sha: str,
    expected_manifest_sha256: str,
    expected_plan_sha256: str,
    expected_approval_sha256: str,
    expected_phase_evidence_schema_sha256: str,
    expected_manifest_artifacts: dict[str, Any],
    expected_role_request_sha256: dict[str, str],
    expected_role_source_artifact_sha256: dict[str, str],
    expected_role_observed_at: dict[str, str],
    expected_dynamic_claim_values: dict[str, Any],
    expected_claim_source_sha256: dict[str, str],
    expected_prior_phase_evidence_sha256: dict[str, str],
    prior_phase_evidence_records: dict[str, dict[str, Any]],
    now: datetime | None = None,
    evidence_file_sha256: str | None = None,
) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != EVIDENCE_FIELDS:
        raise PhaseEvidenceError("phase evidence fields are not exact")
    spec = PHASE_SPEC_BY_NAME.get(expected_phase)
    if spec is None:
        raise PhaseEvidenceError("expected phase is not a precommit phase")
    if document["schema"] != EVIDENCE_SCHEMA:
        raise PhaseEvidenceError("phase evidence schema is invalid")
    if (
        expected_phase_evidence_schema_sha256
        != PHASE_EVIDENCE_CONTRACT_SHA256
        or document["phase_evidence_schema_sha256"]
        != expected_phase_evidence_schema_sha256
    ):
        raise PhaseEvidenceError("phase evidence contract SHA-256 is invalid")

    campaign_id = _canonical_uuid(expected_campaign_id, label="expected campaign_id")
    operation_id = _canonical_uuid(expected_operation_id, label="expected operation_id")
    if campaign_id == operation_id:
        raise PhaseEvidenceError("expected operation_id must differ from campaign_id")
    for value, label in (
        (expected_manifest_sha256, "expected manifest"),
        (expected_plan_sha256, "expected plan"),
        (expected_approval_sha256, "expected approval"),
    ):
        _nonzero_sha256(value, label=label)
    if (
        SHA40_RE.fullmatch(expected_release_sha) is None
        or SHA40_RE.fullmatch(expected_legacy_release_sha) is None
        or expected_release_sha == expected_legacy_release_sha
    ):
        raise PhaseEvidenceError("expected release identities are invalid")
    manifest_artifacts = _validate_manifest_artifacts(
        expected_manifest_artifacts,
        release_sha=expected_release_sha,
    )
    if document["manifest_artifact_bindings"] != manifest_artifacts:
        raise PhaseEvidenceError(
            "phase evidence manifest artifact bindings are invalid"
        )
    if expected_approval_sha256 != manifest_artifacts["cutover_approval_sha256"]:
        raise PhaseEvidenceError("expected approval differs from manifest artifacts")
    if (
        expected_phase_evidence_schema_sha256
        != manifest_artifacts["phase_evidence_schema_sha256"]
    ):
        raise PhaseEvidenceError(
            "expected evidence schema differs from manifest artifacts"
        )
    expected_dynamic_claim_values = _validate_expected_dynamic_claims(
        expected_dynamic_claim_values,
        phase=spec.phase,
    )
    if (
        not isinstance(expected_claim_source_sha256, dict)
        or set(expected_claim_source_sha256) != set(PHASE_CLAIM_RULES[spec.phase])
    ):
        raise PhaseEvidenceError("expected claim source mapping is not exact")
    for name, digest in expected_claim_source_sha256.items():
        _nonzero_sha256(digest, label=f"expected claim source {name}")
    expected_prior_rows = _expected_prior_phase_rows(
        phase=spec.phase,
        expected_digests=expected_prior_phase_evidence_sha256,
    )
    prior_digest_map = {
        row["phase"]: row["evidence_sha256"] for row in expected_prior_rows
    }
    expected_prior_claim_rows = _derive_prior_claim_rows(
        phase=spec.phase,
        prior_digests=prior_digest_map,
        prior_records=prior_phase_evidence_records,
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=expected_release_sha,
        legacy_release_sha=expected_legacy_release_sha,
        manifest_sha256=expected_manifest_sha256,
        plan_sha256=expected_plan_sha256,
    )
    prior_rows = document["prior_phase_evidence"]
    if not isinstance(prior_rows, list) or prior_rows != expected_prior_rows:
        raise PhaseEvidenceError(
            "prior phase evidence does not match the ordered journal prefix"
        )
    if any(
        not isinstance(row, dict)
        or set(row) != PRIOR_PHASE_EVIDENCE_FIELDS
        for row in prior_rows
    ):
        raise PhaseEvidenceError("prior phase evidence fields are not exact")
    prior_closure_sha256 = hashlib.sha256(
        _canonical_json(expected_prior_rows)
    ).hexdigest()
    if (
        document["prior_phase_evidence_closure_sha256"]
        != prior_closure_sha256
    ):
        raise PhaseEvidenceError("prior phase evidence closure SHA-256 is invalid")
    prior_claim_rows = document["prior_claim_bindings"]
    if (
        not isinstance(prior_claim_rows, list)
        or prior_claim_rows != expected_prior_claim_rows
        or any(
            not isinstance(row, dict)
            or set(row) != PRIOR_CLAIM_BINDING_FIELDS
            for row in prior_claim_rows
        )
    ):
        raise PhaseEvidenceError("prior claim bindings are not exact")
    expected_roles = set(spec.roles)
    for mapping, label in (
        (expected_role_request_sha256, "expected role request"),
        (
            expected_role_source_artifact_sha256,
            "expected role source artifact",
        ),
    ):
        if not isinstance(mapping, dict) or set(mapping) != expected_roles:
            raise PhaseEvidenceError(f"{label} mapping is not exact")
        for role, digest in mapping.items():
            _nonzero_sha256(digest, label=f"{label} {role}")
    if (
        not isinstance(expected_role_observed_at, dict)
        or set(expected_role_observed_at) != expected_roles
    ):
        raise PhaseEvidenceError("expected role observation mapping is not exact")
    phase_input_closure = {
        "manifest_sha256": expected_manifest_sha256,
        "manifest_artifacts_sha256": hashlib.sha256(
            _canonical_json(manifest_artifacts)
        ).hexdigest(),
        "prior_phase_evidence": expected_prior_rows,
        "prior_claim_bindings": expected_prior_claim_rows,
        "dynamic_claim_values": expected_dynamic_claim_values,
        "claim_source_sha256": {
            name: expected_claim_source_sha256[name]
            for name in sorted(expected_claim_source_sha256)
        },
        "role_request_sha256": {
            role: expected_role_request_sha256[role] for role in spec.roles
        },
        "role_source_artifact_sha256": {
            role: expected_role_source_artifact_sha256[role]
            for role in spec.roles
        },
        "role_observed_at": {
            role: expected_role_observed_at[role] for role in spec.roles
        },
    }
    phase_input_closure_sha256 = hashlib.sha256(
        _canonical_json(phase_input_closure)
    ).hexdigest()
    if document["phase_input_closure_sha256"] != phase_input_closure_sha256:
        raise PhaseEvidenceError("phase input closure SHA-256 is invalid")

    expected_bindings = {
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": expected_release_sha,
        "legacy_release_sha": expected_legacy_release_sha,
        "manifest_sha256": expected_manifest_sha256,
        "plan_sha256": expected_plan_sha256,
        "approval_sha256": expected_approval_sha256,
        "phase": spec.phase,
        "operation": spec.operation,
        "journal_status": PRECOMMIT_JOURNAL_STATUS,
        "status": "passed",
        "business_write_observed": False,
    }
    for field, expected in expected_bindings.items():
        if not _typed_exact(document[field], expected):
            raise PhaseEvidenceError(f"phase evidence {field} binding is invalid")

    observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    captured_at = _timestamp(document["captured_at"], label="phase evidence")
    if captured_at > observed_now + MAX_FUTURE_SKEW:
        raise PhaseEvidenceError("phase evidence is implausibly in the future")
    phase_max_age = PHASE_MAX_AGE.get(spec.phase, MAX_EVIDENCE_AGE)
    if observed_now - captured_at > phase_max_age:
        raise PhaseEvidenceError("phase evidence is stale")

    attestations = document["role_attestations"]
    if not isinstance(attestations, list) or len(attestations) != len(spec.roles):
        raise PhaseEvidenceError("role attestation count is invalid")
    observed_roles: list[str] = []
    for expected_role, attestation in zip(spec.roles, attestations, strict=True):
        if (
            not isinstance(attestation, dict)
            or set(attestation) != ROLE_ATTESTATION_FIELDS
        ):
            raise PhaseEvidenceError("role attestation fields are not exact")
        topology = EXPECTED_TOPOLOGY[expected_role]
        expected_role_values = {
            "role": expected_role,
            "expected_host": topology["host"],
            "operation": spec.operation,
            "app_release_sha": expected_release_sha,
            "agent_artifact_sha256": manifest_artifacts[
                "host_agent_sha256"
            ],
            "host_identity_observed": True,
            "observed_at": expected_role_observed_at[expected_role],
            "status": "verified",
            "transport": topology["transport"],
        }
        for field, expected in expected_role_values.items():
            if not _typed_exact(attestation[field], expected):
                raise PhaseEvidenceError(
                    f"{expected_role} attestation {field} is invalid"
                )
        if (
            attestation["request_sha256"]
            != expected_role_request_sha256[expected_role]
        ):
            raise PhaseEvidenceError(
                f"{expected_role} request SHA-256 differs from controller intent"
            )
        if (
            attestation["source_artifact_sha256"]
            != expected_role_source_artifact_sha256[expected_role]
        ):
            raise PhaseEvidenceError(
                f"{expected_role} source artifact SHA-256 differs from readback"
            )
        role_time = _timestamp(
            attestation["observed_at"],
            label=f"{expected_role} observation",
        )
        if role_time > observed_now + MAX_FUTURE_SKEW:
            raise PhaseEvidenceError(f"{expected_role} observation is in the future")
        allowed_role_skew = min(MAX_ROLE_CAPTURE_SKEW, phase_max_age)
        if abs(role_time - captured_at) > allowed_role_skew:
            raise PhaseEvidenceError(
                f"{expected_role} observation is outside phase capture skew"
            )
        observed_roles.append(expected_role)

    rules = PHASE_CLAIM_RULES[spec.phase]
    claims = document["claims"]
    if not isinstance(claims, dict) or set(claims) != set(rules):
        raise PhaseEvidenceError("phase claim set is not exact")
    for name, rule in rules.items():
        _validate_claim(name, claims[name], rule)
        if claims[name]["source_sha256"] != expected_claim_source_sha256[name]:
            raise PhaseEvidenceError(
                f"claim {name} source differs from the secure source artifact"
            )
        if rule.kind != "exact" and not _typed_exact(
            claims[name]["value"],
            expected_dynamic_claim_values[name],
        ):
            raise PhaseEvidenceError(
                f"claim {name} differs from executor expected input"
            )
    for claim_name, artifact_name in PHASE_MANIFEST_CLAIM_BINDINGS.get(
        spec.phase,
        {},
    ).items():
        if claims[claim_name]["value"] != _manifest_artifact_binding_value(
            manifest_artifacts,
            artifact_name,
        ):
            raise PhaseEvidenceError(
                f"claim {claim_name} differs from manifest artifact {artifact_name}"
            )
    for row in expected_prior_claim_rows:
        if claims[row["target_claim"]]["value"] != row["value"]:
            raise PhaseEvidenceError(
                f"claim {row['target_claim']} differs from prior verified claim"
            )

    digest = (
        _nonzero_sha256(evidence_file_sha256, label="evidence file")
        if evidence_file_sha256 is not None
        else hashlib.sha256(_canonical_json(document)).hexdigest()
    )
    return {
        "schema": VERIFICATION_SCHEMA,
        "status": "verified",
        "phase": spec.phase,
        "operation": spec.operation,
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": expected_release_sha,
        "legacy_release_sha": expected_legacy_release_sha,
        "manifest_sha256": expected_manifest_sha256,
        "plan_sha256": expected_plan_sha256,
        "approval_sha256": expected_approval_sha256,
        "phase_evidence_schema_sha256": PHASE_EVIDENCE_CONTRACT_SHA256,
        "manifest_artifact_bindings_sha256": hashlib.sha256(
            _canonical_json(manifest_artifacts)
        ).hexdigest(),
        "prior_phase_evidence_closure_sha256": prior_closure_sha256,
        "phase_input_closure_sha256": phase_input_closure_sha256,
        "prior_phase_count": len(expected_prior_rows),
        "evidence_sha256": digest,
        "verified_roles": observed_roles,
        "verified_claim_count": len(rules),
        "captured_at": document["captured_at"],
        "verified_at": observed_now.isoformat(),
        "production_contacted": False,
    }


def read_root_only_evidence(
    path: Path,
    *,
    owner_uid: int = 0,
    max_size: int = 16 * 1024 * 1024,
) -> tuple[dict[str, Any], str]:
    try:
        payload = read_secure_bytes(
            path,
            label="production shadow phase evidence",
            owner_uid=owner_uid,
            max_size=max_size,
        )
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (SecureFileError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhaseEvidenceError("phase evidence is not secure strict JSON") from exc
    if not isinstance(document, dict):
        raise PhaseEvidenceError("phase evidence root must be an object")
    return document, hashlib.sha256(payload).hexdigest()


def hash_release_verifier(path: Path, *, owner_uid: int = 0) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
        os,
        "O_NOFOLLOW",
        0,
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PhaseEvidenceError("cannot securely open phase evidence verifier") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > 4 * 1024 * 1024
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise PhaseEvidenceError("phase evidence verifier artifact is unsafe")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > 4 * 1024 * 1024:
                raise PhaseEvidenceError("phase evidence verifier artifact is oversized")
            digest.update(chunk)
        after = os.fstat(descriptor)
        stable = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
            "st_size",
        )
        if any(
            getattr(before, field) != getattr(after, field) for field in stable
        ):
            raise PhaseEvidenceError(
                "phase evidence verifier changed while being hashed"
            )
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _parse_role_hashes(values: list[str], *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        role, separator, digest = value.partition("=")
        if not separator or not role or role in result:
            raise PhaseEvidenceError(f"{label} mapping is invalid")
        result[role] = _nonzero_sha256(digest, label=f"{label} {role}")
    return result


def _parse_string_mapping(values: list[str], *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, mapped = value.partition("=")
        if not separator or not key or not mapped or key in result:
            raise PhaseEvidenceError(f"{label} mapping is invalid")
        result[key] = mapped
    return result


def _parse_json_mapping(values: list[str], *, label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        key, separator, encoded = value.partition("=")
        if not separator or not key or not encoded or key in result:
            raise PhaseEvidenceError(f"{label} mapping is invalid")
        try:
            result[key] = json.loads(encoded, object_pairs_hook=_strict_object)
        except (json.JSONDecodeError, PhaseEvidenceError) as exc:
            raise PhaseEvidenceError(f"{label} value for {key} is invalid") from exc
    return result


def _read_prior_evidence_records(values: list[str]) -> dict[str, dict[str, Any]]:
    paths = _parse_string_mapping(values, label="prior phase evidence file")
    result: dict[str, dict[str, Any]] = {}
    for phase, raw_path in paths.items():
        document, digest = read_root_only_evidence(Path(raw_path))
        result[phase] = {"document": document, "file_sha256": digest}
    return result


def _read_secure_json_record(
    path: Path,
    *,
    label: str,
    max_size: int = 16 * 1024 * 1024,
) -> tuple[dict[str, Any], str]:
    try:
        payload = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=max_size,
        )
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (SecureFileError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhaseEvidenceError(f"{label} is not secure strict JSON") from exc
    if not isinstance(document, dict):
        raise PhaseEvidenceError(f"{label} root must be an object")
    return document, hashlib.sha256(payload).hexdigest()


def _read_role_validation_records(
    values: list[str],
    *,
    phase: str,
    manifest: dict[str, Any],
    manifest_sha256: str,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    spec = PHASE_SPEC_BY_NAME[phase]
    paths = _parse_string_mapping(values, label="role validation file")
    if set(paths) != set(spec.roles):
        raise PhaseEvidenceError("role validation file mapping is not exact")
    request_hashes: dict[str, str] = {}
    source_hashes: dict[str, str] = {}
    observed_at: dict[str, str] = {}
    for role in spec.roles:
        document, source_sha256 = _read_secure_json_record(
            Path(paths[role]),
            label=f"{role} host-agent validation",
        )
        if set(document) != HOST_AGENT_VALIDATION_FIELDS:
            raise PhaseEvidenceError(
                f"{role} host-agent validation fields are not exact"
            )
        topology = EXPECTED_TOPOLOGY[role]
        expected = {
            "schema": "production-shadow-host-agent-validation-v1",
            "status": "validated-request",
            "operation": spec.operation,
            "role": role,
            "campaign_id": manifest["campaign_id"],
            "operation_id": manifest["operation_id"],
            "app_release_sha": manifest["release_sha"],
            "manifest_sha256": manifest_sha256,
            "approval_sha256": manifest["artifacts"]["cutover_approval_sha256"],
            "expected_host": topology["host"],
            "observed_host": topology["host"],
            "required_journal_status": PRECOMMIT_JOURNAL_STATUS,
            "business_write_policy": "forbid",
            "agent_artifact_sha256": manifest["artifacts"]["host_agent_sha256"],
            "host_agent_contract_sha256": manifest["artifacts"][
                "host_agent_contract_sha256"
            ],
            "transport": topology["transport"],
            "host_identity_observed": True,
            "execution_supported": False,
            "production_contacted": False,
        }
        if any(
            not _typed_exact(document[field], expected_value)
            for field, expected_value in expected.items()
        ):
            raise PhaseEvidenceError(
                f"{role} host-agent validation binding is invalid"
            )
        request_hashes[role] = _nonzero_sha256(
            document["request_sha256"],
            label=f"{role} request",
        )
        observed_at[role] = str(document["observed_at"])
        _timestamp(observed_at[role], label=f"{role} host-agent validation")
        source_hashes[role] = source_sha256
    return request_hashes, source_hashes, observed_at


def _read_claim_source_records(
    values: list[str],
    *,
    phase: str,
    manifest: dict[str, Any],
    manifest_sha256: str,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, str]]:
    spec = PHASE_SPEC_BY_NAME[phase]
    rules = PHASE_CLAIM_RULES[phase]
    paths = _parse_string_mapping(values, label="claim source file")
    if set(paths) != set(rules):
        raise PhaseEvidenceError("claim source file mapping is not exact")
    dynamic_values: dict[str, Any] = {}
    source_hashes: dict[str, str] = {}
    phase_max_age = PHASE_MAX_AGE.get(phase, MAX_EVIDENCE_AGE)
    for claim, rule in rules.items():
        document, source_sha256 = _read_secure_json_record(
            Path(paths[claim]),
            label=f"{phase} claim source {claim}",
        )
        if set(document) != CLAIM_SOURCE_FIELDS:
            raise PhaseEvidenceError(f"claim source {claim} fields are not exact")
        expected = {
            "schema": "production-shadow-phase-claim-source-v1",
            "campaign_id": manifest["campaign_id"],
            "operation_id": manifest["operation_id"],
            "release_sha": manifest["release_sha"],
            "manifest_sha256": manifest_sha256,
            "phase": phase,
            "operation": spec.operation,
            "claim": claim,
            "status": "observed",
        }
        if any(
            not _typed_exact(document[field], expected_value)
            for field, expected_value in expected.items()
        ):
            raise PhaseEvidenceError(f"claim source {claim} binding is invalid")
        _validate_claim(
            claim,
            {"value": document["value"], "source_sha256": source_sha256},
            rule,
        )
        observed_at = _timestamp(
            document["observed_at"],
            label=f"claim source {claim}",
        )
        if observed_at > now + MAX_FUTURE_SKEW or now - observed_at > phase_max_age:
            raise PhaseEvidenceError(f"claim source {claim} is not fresh")
        if rule.kind != "exact":
            dynamic_values[claim] = document["value"]
        source_hashes[claim] = source_sha256
    return dynamic_values, source_hashes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--expected-phase", required=True)
    parser.add_argument("--role-validation", action="append", default=[])
    parser.add_argument("--claim-source", action="append", default=[])
    parser.add_argument("--prior-phase-evidence", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise PhaseEvidenceError("phase evidence verifier must run as root")
        manifest, manifest_sha256 = read_root_only_manifest(args.manifest)
        plan = render_plan(
            manifest,
            manifest_sha256=manifest_sha256,
            manifest_path=args.manifest,
        )
        approval_sha256, _ = sha256_secure_file(
            args.approval,
            label="production cutover approval",
            owner_uid=0,
            max_size=16 * 1024 * 1024,
        )
        if approval_sha256 != manifest["artifacts"]["cutover_approval_sha256"]:
            raise PhaseEvidenceError(
                "approval file differs from the manifest artifact"
            )
        verifier_sha256 = hash_release_verifier(Path(__file__).resolve())
        if (
            verifier_sha256
            != manifest["artifacts"]["phase_evidence_verifier_sha256"]
        ):
            raise PhaseEvidenceError(
                "phase evidence verifier differs from the manifest artifact"
            )
        journal = ProductionCutoverJournal(
            Path(manifest["deployment"]["controller_journal_path"])
        )
        journal_state = journal.assert_bindings(
            manifest_sha256=manifest_sha256,
            plan_sha256=plan["plan_sha256"],
            campaign_id=manifest["campaign_id"],
            operation_id=manifest["operation_id"],
            release_sha=manifest["release_sha"],
            legacy_release_sha=manifest["legacy_release_sha"],
        )
        if args.expected_phase not in PHASES:
            raise PhaseEvidenceError("expected phase is not a precommit phase")
        phase_index = PHASES.index(args.expected_phase)
        expected_prefix = list(PHASES[:phase_index])
        if (
            journal_state["status"] != "phase_started"
            or journal_state["started_phase"] != args.expected_phase
            or journal_state["completed_phases"] != expected_prefix
            or set(journal_state["phase_evidence_sha256"])
            != set(expected_prefix)
        ):
            raise PhaseEvidenceError(
                "journal is not durably started at the exact evidence phase"
            )
        (
            role_request_sha256,
            role_source_artifact_sha256,
            role_observed_at,
        ) = _read_role_validation_records(
            args.role_validation,
            phase=args.expected_phase,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
        )
        observed_now = datetime.now(timezone.utc)
        (
            dynamic_claim_values,
            claim_source_sha256,
        ) = _read_claim_source_records(
            args.claim_source,
            phase=args.expected_phase,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            now=observed_now,
        )
        document, file_sha256 = read_root_only_evidence(args.evidence)
        result = verify_phase_evidence(
            document,
            expected_phase=args.expected_phase,
            expected_campaign_id=manifest["campaign_id"],
            expected_operation_id=manifest["operation_id"],
            expected_release_sha=manifest["release_sha"],
            expected_legacy_release_sha=manifest["legacy_release_sha"],
            expected_manifest_sha256=manifest_sha256,
            expected_plan_sha256=plan["plan_sha256"],
            expected_approval_sha256=approval_sha256,
            expected_phase_evidence_schema_sha256=manifest["artifacts"][
                "phase_evidence_schema_sha256"
            ],
            expected_manifest_artifacts=manifest["artifacts"],
            expected_role_request_sha256=role_request_sha256,
            expected_role_source_artifact_sha256=(
                role_source_artifact_sha256
            ),
            expected_role_observed_at=role_observed_at,
            expected_dynamic_claim_values=dynamic_claim_values,
            expected_claim_source_sha256=claim_source_sha256,
            expected_prior_phase_evidence_sha256=_parse_role_hashes(
                [
                    f"{phase}={journal_state['phase_evidence_sha256'][phase]}"
                    for phase in expected_prefix
                ],
                label="journal prior phase evidence",
            ),
            prior_phase_evidence_records=_read_prior_evidence_records(
                args.prior_phase_evidence,
            ),
            now=observed_now,
            evidence_file_sha256=file_sha256,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "production_contacted": False,
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
