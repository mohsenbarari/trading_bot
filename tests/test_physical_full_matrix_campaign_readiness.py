from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import pickle
import unittest
from unittest.mock import patch
from uuid import UUID

from core.external_effect_execution_gate import (
    EXTERNAL_EFFECT_EXECUTION_SCOPES,
    ExternalEffectExecutionAuthorization,
)
import core.physical_full_matrix_campaign_readiness as readiness
from core.physical_arvan_immutability_preflight import (
    PhysicalArvanCredentialRestrictionObservation,
    PhysicalArvanDeniedOperationObservation,
    PhysicalArvanDisposableImmutabilityProbe,
    PhysicalArvanImmutabilityPreflightBinding,
    build_physical_arvan_immutability_preflight_observation,
    verify_physical_arvan_immutability_preflight,
)
from core.physical_arvan_s3_four_role_immutability_preflight import (
    PhysicalArvanS3FourRoleImmutableVersionObservation,
    PhysicalArvanS3FourRoleImmutabilityDirectionObservation,
    PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
    PhysicalArvanS3FourRoleImmutabilityPreflightConfig,
    build_physical_arvan_s3_four_role_immutability_preflight_observation,
    derive_physical_arvan_s3_four_role_immutability_probe_object_key,
    verify_physical_arvan_s3_four_role_immutability_preflight,
)
from core.physical_ir_to_fi_object_storage_failback_preflight import (
    PhysicalIrToFiObjectStorageFailbackBinding,
    PhysicalIrToFiObjectStorageFailbackPreflightConfig,
    build_physical_ir_to_fi_object_storage_failback_observation,
    verify_physical_ir_to_fi_object_storage_failback_preflight,
)
from core.physical_full_matrix_campaign_readiness import (
    PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED,
    PHYSICAL_FULL_MATRIX_SOURCE_WRITE_FENCE_MODE,
    PHYSICAL_FULL_MATRIX_RECOVERY_ROUTE,
    PHYSICAL_FULL_MATRIX_SOURCE_FENCE_RECOVERY_ROUTE_SCHEMA,
    PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_RECEIVER_RESPONSE_SOURCE,
    PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_WRITER_RESPONSE_MODE,
    PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA,
    PhysicalFullMatrixCampaignBinding,
    PhysicalFullMatrixCampaignInputs,
    PhysicalFullMatrixCampaignReadinessConfig,
    PhysicalFullMatrixCampaignReadinessError,
    PhysicalFullMatrixDeploymentPreflightPosture,
    PhysicalFullMatrixSourceFenceRecoveryRouteObservation,
    PhysicalFullMatrixStrictRemoteAckWriterResponseObservation,
    require_verified_physical_full_matrix_deployment_preflight_posture,
    require_verified_physical_full_matrix_external_effect_reconciliation,
    require_verified_physical_full_matrix_source_fence_recovery_route,
    require_verified_physical_full_matrix_strict_remote_ack_writer_response,
    verify_physical_full_matrix_deployment_preflight_posture,
    verify_physical_full_matrix_external_effect_reconciliation,
    verify_physical_full_matrix_source_fence_recovery_route,
    verify_physical_full_matrix_strict_remote_ack_writer_response,
)
from tests.physical_arvan_s3_four_role_fixture import make_four_role_fixture
from tests.physical_arvan_s3_four_role_live_iam_fixture import (
    make_four_role_live_iam_durable_admission_fixture,
)
from tests.test_physical_full_matrix_v2_recovery_evidence import (
    PhysicalFullMatrixV2RecoveryEvidenceTests as _V2RecoveryEvidenceFixture,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
DEPLOYMENT_OPERATION_ID = "f6d5dabe-9c52-4517-b6de-7ebbc55355c9"
P0_OPERATION_ID = UUID("4a9ed217-a69a-4b1a-b1f0-9db8c35fb802")


def binding() -> PhysicalFullMatrixCampaignBinding:
    return PhysicalFullMatrixCampaignBinding(
        campaign_id="physical-full-matrix-20260731",
        release_sha="a" * 40,
        schema_revision="alembic-20260731",
        source_site="webapp_fi",
        destination_site="webapp_ir",
        baseline_generation_id="baseline-20260731",
        baseline_manifest_sha256="b" * 64,
        baseline_wal_lsn="0/10",
        timeline_id=1,
        stream_generation_id="stream-20260731",
        destination_age_recipient="age1" + "a" * 20,
        route_binding_sha256="c" * 64,
        writer_epoch=7,
        writer_lease_id="lease-20260731",
        witness_transition_id="transition-20260731",
        witnessed_term_proof_sha256="d" * 64,
        target_acknowledged_wal_lsn="0/20",
        blob_object_frontier_wal_lsn="0/20",
        recovery_stage_bundle_id="e" * 64,
        recovery_stage_receipt_sha256="f" * 64,
        deployment_operation_id=DEPLOYMENT_OPERATION_ID,
        deployment_manifest_sha256="1" * 64,
        p0_operation_id=P0_OPERATION_ID,
    )


def enabled_config() -> PhysicalFullMatrixCampaignReadinessConfig:
    return PhysicalFullMatrixCampaignReadinessConfig(binding=binding(), enabled=True)


def arvan_immutability_binding() -> PhysicalArvanImmutabilityPreflightBinding:
    item = binding()
    return PhysicalArvanImmutabilityPreflightBinding(
        campaign_id=item.campaign_id,
        release_sha=item.release_sha,
        source_site=item.source_site,
        destination_site=item.destination_site,
        route_binding_sha256=item.route_binding_sha256,
        endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
        region="ir-thr-at1",
        bucket="private-physical-recovery",
        minimum_retention_days=90,
    )


def arvan_immutability_preflight():
    denied = lambda *operations: tuple(
        PhysicalArvanDeniedOperationObservation(operation=operation, outcome="access-denied")
        for operation in operations
    )
    restrictions = (
        PhysicalArvanCredentialRestrictionObservation(
            role="fi-publisher",
            credential_posture="scoped-credential-probed",
            credential_identity_sha256="9" * 64,
            allowed_operations=(
                "GetBucketAcl",
                "GetBucketVersioning",
                "GetObjectLockConfiguration",
                "PutObject:create-only",
                "ListObjectVersions:exact-key",
                "GetObjectRetention:exact-version",
                "GetObject:exact-version",
                "HeadObject:exact-version",
            ),
            denied_operations=denied(
                "DeleteObject", "DeleteObjectVersion", "PutObject:overwrite"
            ),
        ),
        PhysicalArvanCredentialRestrictionObservation(
            role="ir-receiver",
            credential_posture="scoped-credential-probed",
            credential_identity_sha256="8" * 64,
            allowed_operations=("GetObject:exact-version", "HeadObject:exact-version"),
            denied_operations=denied(
                "DeleteObject",
                "DeleteObjectVersion",
                "ListBucket",
                "ListObjectVersions",
                "PutObject",
            ),
        ),
        PhysicalArvanCredentialRestrictionObservation(
            role="witness-controller",
            credential_posture="no-object-storage-credential-issued",
            credential_identity_sha256=None,
            allowed_operations=(),
            denied_operations=(),
        ),
    )
    raw = build_physical_arvan_immutability_preflight_observation(
        binding=arvan_immutability_binding(),
        versioning_status="Enabled",
        acl_posture="private-canonical-owner-only-v1",
        retention_mode="provider-verified-immutable-retention-v1",
        retention_policy_evidence_sha256="7" * 64,
        retention_days=180,
        credential_restrictions=restrictions,
        disposable_probe=PhysicalArvanDisposableImmutabilityProbe(
            object_key=(
                "physical-preflight/physical-full-matrix-20260731/"
                "arvan-immutability/nonce-20260731.age"
            ),
            version_id="arvan-preflight-version-20260731",
            ciphertext_sha256="6" * 64,
            ciphertext_bytes=512,
            delete_version_outcome="access-denied",
            delete_marker_outcome="access-denied",
            exact_version_get_outcome="exact-version-get-succeeded",
            retrieved_version_id="arvan-preflight-version-20260731",
            retrieved_ciphertext_sha256="6" * 64,
            retrieved_ciphertext_bytes=512,
        ),
        observed_at=NOW,
    )
    return verify_physical_arvan_immutability_preflight(
        raw, binding=arvan_immutability_binding(), now=NOW
    )


def arvan_failback_binding(**overrides: object) -> PhysicalIrToFiObjectStorageFailbackBinding:
    item = binding()
    fixture = make_four_role_fixture(
        campaign_id=item.campaign_id,
        release_sha=item.release_sha,
        fi_publisher_identity_sha256="2" * 64,
        ir_receiver_identity_sha256="3" * 64,
        ir_publisher_identity_sha256="4" * 64,
        fi_receiver_identity_sha256="5" * 64,
    )
    return replace(fixture.binding, **overrides)


def arvan_failback_preflight(
    selected: PhysicalIrToFiObjectStorageFailbackBinding | None = None,
    *,
    include_context: bool = False,
):
    selected = selected or arvan_failback_binding()
    fixture = make_four_role_fixture(
        campaign_id=selected.campaign_id,
        release_sha=selected.release_sha,
        fi_publisher_identity_sha256=selected.fi_publisher_identity_sha256,
        ir_receiver_identity_sha256=selected.ir_receiver_identity_sha256,
        ir_publisher_identity_sha256=selected.ir_publisher_identity_sha256,
        fi_receiver_identity_sha256=selected.fi_receiver_identity_sha256,
    )
    live_iam = make_four_role_live_iam_durable_admission_fixture(
        binding=selected,
        observed_at=NOW,
    )
    observation = build_physical_ir_to_fi_object_storage_failback_observation(
        binding=selected,
        four_role_projection_binding=fixture.verified_binding,
        four_role_live_iam_binding=live_iam.live_iam_binding,
        four_role_live_iam_durable_admission=live_iam.live_iam_durable_admission,
        observed_at=NOW,
    )
    verified = verify_physical_ir_to_fi_object_storage_failback_preflight(
        observation,
        binding=selected,
        four_role_projection_binding=fixture.verified_binding,
        four_role_live_iam_binding=live_iam.live_iam_binding,
        four_role_live_iam_durable_admission=live_iam.live_iam_durable_admission,
        now=NOW,
    )
    return (verified, fixture, live_iam) if include_context else verified


def arvan_four_role_immutability_preflight(
    selected: PhysicalIrToFiObjectStorageFailbackBinding | None = None,
):
    """Build one real opaque immutable-storage proof for readiness tests."""

    selected = selected or arvan_failback_binding()
    live_iam = make_four_role_live_iam_durable_admission_fixture(
        binding=selected,
        observed_at=NOW,
    )
    immutable_binding = PhysicalArvanS3FourRoleImmutabilityPreflightBinding(
        campaign_id=selected.campaign_id,
        release_sha=selected.release_sha,
        endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
        region="ir-thr-at1",
        bucket="private-four-role-immutability",
        bucket_access_posture="private",
        normal_object_storage_namespace="physical-wal",
        reverse_object_storage_namespace="physical-failback",
        minimum_retention_days=90,
        normal_route_scope_sha256=selected.normal_route_scope_sha256,
        reverse_route_scope_sha256=selected.reverse_route_scope_sha256,
        four_role_route_binding_sha256=selected.route_binding_sha256,
        fi_publisher_identity_sha256=selected.fi_publisher_identity_sha256,
        ir_receiver_identity_sha256=selected.ir_receiver_identity_sha256,
        ir_publisher_identity_sha256=selected.ir_publisher_identity_sha256,
        fi_receiver_identity_sha256=selected.fi_receiver_identity_sha256,
    )
    configuration = PhysicalArvanS3FourRoleImmutabilityPreflightConfig(
        binding=immutable_binding,
        enabled=True,
        maximum_evidence_age_seconds=90,
    )

    def direction(
        *,
        name: str,
        publisher_role: str,
        receiver_role: str,
        namespace: str,
        publisher_identity: str,
        receiver_identity: str,
        nonce: str,
        content_sha256: str,
        version: str,
    ) -> PhysicalArvanS3FourRoleImmutabilityDirectionObservation:
        key = derive_physical_arvan_s3_four_role_immutability_probe_object_key(
            binding=immutable_binding,
            direction=name,
            probe_nonce_sha256=nonce,
        )
        immutable_version = PhysicalArvanS3FourRoleImmutableVersionObservation(
            probe_nonce_sha256=nonce,
            object_key=key,
            object_version_id=version,
            content_sha256=content_sha256,
            content_bytes=4096,
            retention_until=NOW + timedelta(days=91),
            exact_head_version_id=version,
            exact_get_version_id=version,
            exact_get_content_sha256=content_sha256,
            exact_get_content_bytes=4096,
        )
        return PhysicalArvanS3FourRoleImmutabilityDirectionObservation(
            direction=name,
            publisher_role=publisher_role,
            receiver_role=receiver_role,
            object_storage_namespace=namespace,
            publisher_identity_sha256=publisher_identity,
            receiver_identity_sha256=receiver_identity,
            acl_posture="private-canonical-owner-only-v1",
            versioning_status="Enabled",
            retention_mode="s3-object-lock-compliance-v1",
            retention_policy_evidence_sha256="9" * 64,
            retention_days=90,
            immutable_version=immutable_version,
            publisher_create_only_outcome="create-only-succeeded",
            publisher_overwrite_outcome="access-denied",
            publisher_delete_object_outcome="access-denied",
            publisher_delete_version_outcome="access-denied",
            receiver_exact_head_outcome="exact-version-head-succeeded",
            receiver_exact_get_outcome="exact-version-get-succeeded",
            receiver_put_outcome="access-denied",
            receiver_delete_object_outcome="access-denied",
            receiver_delete_version_outcome="access-denied",
            receiver_list_bucket_outcome="access-denied",
            receiver_list_versions_outcome="access-denied",
        )

    normal = direction(
        name="fi-publisher-to-ir-receiver",
        publisher_role="fi-publisher",
        receiver_role="ir-receiver",
        namespace="physical-wal",
        publisher_identity=selected.fi_publisher_identity_sha256,
        receiver_identity=selected.ir_receiver_identity_sha256,
        nonce="a" * 64,
        content_sha256="b" * 64,
        version="version-normal-001",
    )
    reverse = direction(
        name="ir-publisher-to-fi-receiver",
        publisher_role="ir-publisher",
        receiver_role="fi-receiver",
        namespace="physical-failback",
        publisher_identity=selected.ir_publisher_identity_sha256,
        receiver_identity=selected.fi_receiver_identity_sha256,
        nonce="c" * 64,
        content_sha256="d" * 64,
        version="version-reverse-001",
    )
    observation = build_physical_arvan_s3_four_role_immutability_preflight_observation(
        binding=immutable_binding,
        admission=live_iam.live_iam_durable_admission,
        live_iam_binding=live_iam.live_iam_binding,
        failback_binding=selected,
        normal_direction=normal,
        reverse_direction=reverse,
        observed_at=NOW,
    )
    verified = verify_physical_arvan_s3_four_role_immutability_preflight(
        observation,
        config=configuration,
        admission=live_iam.live_iam_durable_admission,
        live_iam_binding=live_iam.live_iam_binding,
        failback_binding=selected,
        observed_at=NOW,
    )
    return verified, configuration, live_iam, selected


def common_observation_fields() -> dict[str, object]:
    item = binding()
    return {
        "campaign_id": item.campaign_id,
        "release_sha": item.release_sha,
        "schema_revision": item.schema_revision,
        "source_site": item.source_site,
        "destination_site": item.destination_site,
        "baseline_generation_id": item.baseline_generation_id,
        "baseline_manifest_sha256": item.baseline_manifest_sha256,
        "baseline_wal_lsn": item.baseline_wal_lsn,
        "timeline_id": item.timeline_id,
        "stream_generation_id": item.stream_generation_id,
        "destination_age_recipient": item.destination_age_recipient,
        "route_binding_sha256": item.route_binding_sha256,
        "writer_epoch": item.writer_epoch,
        "writer_lease_id": item.writer_lease_id,
        "witness_transition_id": item.witness_transition_id,
        "witnessed_term_proof_sha256": item.witnessed_term_proof_sha256,
    }


def source_fence_observation(**overrides: object) -> PhysicalFullMatrixSourceFenceRecoveryRouteObservation:
    values: dict[str, object] = {
        **common_observation_fields(),
        "schema": PHYSICAL_FULL_MATRIX_SOURCE_FENCE_RECOVERY_ROUTE_SCHEMA,
        "status": "observed",
        "source_write_fence_mode": PHYSICAL_FULL_MATRIX_SOURCE_WRITE_FENCE_MODE,
        "recovery_route": PHYSICAL_FULL_MATRIX_RECOVERY_ROUTE,
        "direct_fi_to_ir_control": "forbidden",
        "legacy_runner_compatibility": "forbidden",
        "observed_at": NOW,
        "evidence_sha256": "2" * 64,
    }
    values.update(overrides)
    return PhysicalFullMatrixSourceFenceRecoveryRouteObservation(**values)


def strict_writer_response(**overrides: object) -> PhysicalFullMatrixStrictRemoteAckWriterResponseObservation:
    item = binding()
    values: dict[str, object] = {
        **common_observation_fields(),
        "schema": PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA,
        "status": "observed",
        "target_acknowledged_wal_lsn": item.target_acknowledged_wal_lsn,
        "blob_object_frontier_wal_lsn": item.blob_object_frontier_wal_lsn,
        "writer_response_mode": PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_WRITER_RESPONSE_MODE,
        "receiver_response_source": PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_RECEIVER_RESPONSE_SOURCE,
        "durable_commit_coupled": True,
        "fences_writes_when_ack_unavailable": True,
        "observed_at": NOW,
        "evidence_sha256": "3" * 64,
    }
    values.update(overrides)
    return PhysicalFullMatrixStrictRemoteAckWriterResponseObservation(**values)


def external_effect_authorization(**overrides: object) -> ExternalEffectExecutionAuthorization:
    values: dict[str, object] = {
        "authorization_id": "external-effects-20260731",
        "holder_site": "webapp_fi",
        "writer_epoch": 7,
        "writer_lease_id": "lease-20260731",
        "writer_term_issued_at": NOW - timedelta(seconds=30),
        "writer_term_expires_at": NOW + timedelta(seconds=90),
        "witness_transition_id": "transition-20260731",
        "authorized_scopes": tuple(sorted(EXTERNAL_EFFECT_EXECUTION_SCOPES)),
        "reconciliation_decision": "reconciliation_complete_no_resend",
        "reconciliation_evidence_sha256": "4" * 64,
        "reconciliation_completed_at": NOW - timedelta(seconds=15),
        "issued_at": NOW - timedelta(seconds=5),
        "expires_at": NOW + timedelta(seconds=60),
    }
    values.update(overrides)
    return ExternalEffectExecutionAuthorization(**values)


def deployment_posture() -> PhysicalFullMatrixDeploymentPreflightPosture:
    item = binding()
    role_instances = {
        "bot_fi": ("baf42d90-4f4d-4bb7-8d2c-fec3c11bcb9e", "8.8.8.8"),
        "webapp_fi": ("01e4a6e2-78a4-4a6a-a7f5-1a7791a16641", "1.1.1.1"),
        "webapp_ir": ("de4ce67a-8f32-4bb2-bf04-7cbfa10a8cda", "9.9.9.9"),
        "witness": ("cf614b3e-8828-45ae-9691-60780da74a34", "208.67.222.222"),
    }
    roles = [
        {"role": role, "instance_id": instance, "public_ipv4": address}
        for role, (instance, address) in role_instances.items()
    ]
    manifest = {
        "schema": "three-site-dedicated-host-preflight-manifest-binding-v2",
        "status": "validated",
        "campaign_id": item.campaign_id,
        "operation_id": item.deployment_operation_id,
        "release_sha": item.release_sha,
        "manifest_sha256": item.deployment_manifest_sha256,
        "roles": roles,
    }
    observed_at = NOW.isoformat().replace("+00:00", "Z")
    receipts = []
    for role, (instance, address) in role_instances.items():
        receipts.append(
            {
                "schema": "three-site-dedicated-host-preflight-receipt-v2",
                "status": "observed",
                "observation_mode": "read-only",
                "campaign_id": item.campaign_id,
                "operation_id": item.deployment_operation_id,
                "release_sha": item.release_sha,
                "role": role,
                "instance": {
                    "provider": "arvan_ecc",
                    "server_id": instance,
                    "public_ipv4": address,
                },
                "manifest_sha256": item.deployment_manifest_sha256,
                "observed_at": observed_at,
                "observation": {
                    "role_marker": role,
                    "release": {
                        "state": "present",
                        "release_sha": item.release_sha,
                        "clean": True,
                    },
                    "runtime": {
                        "docker_state": "active",
                        "container_count": 0,
                        "matrix_process_count": 0,
                        "current_link_present": True,
                    },
                    "staging_mount": {
                        "present": True,
                        "filesystem": "ext4",
                        "available_bytes": 50_000_000_000,
                        "options": ["nodev", "noexec", "nosuid", "rw"],
                    },
                },
            }
        )
    return PhysicalFullMatrixDeploymentPreflightPosture(
        validated_manifest=manifest,
        receipts=receipts,
    )


class PhysicalFullMatrixCampaignReadinessTests(unittest.TestCase):
    def test_verified_readiness_requires_positive_mint_and_execution_time_reassessment(self) -> None:
        facts = readiness._normalise_binding(binding())
        positive = readiness.PhysicalFullMatrixCampaignReadiness(
            schema=readiness.PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA,
            status=readiness.PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED,
            reason_codes=(),
            campaign_id=facts.campaign_id,
            release_sha=facts.release_sha,
            binding_sha256=facts.binding_sha256,
            observed_slots=("synthetic-positive-readiness-test-slot",),
        )
        blocked = replace(
            positive,
            status=PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED,
            reason_codes=("evidence-no-longer-current",),
        )

        # A public report does not carry provenance, and neither does a
        # caller-constructed wrapper.  Only minting after a positive assessor
        # result may register the process-local state.
        with self.assertRaisesRegex(
            PhysicalFullMatrixCampaignReadinessError,
            "CAPABILITY_REQUIRED",
        ):
            readiness.require_verified_physical_full_matrix_campaign_readiness(
                positive,
                now=NOW,
            )
        with self.assertRaisesRegex(
            PhysicalFullMatrixCampaignReadinessError,
            "CAPABILITY_REQUIRED",
        ):
            readiness.require_verified_physical_full_matrix_campaign_readiness(
                readiness.VerifiedPhysicalFullMatrixCampaignReadiness(report=positive),
                now=NOW,
            )

        with patch.object(
            readiness,
            "assess_physical_full_matrix_campaign_readiness",
            side_effect=(positive, positive),
        ) as assessor:
            verified = readiness.mint_verified_physical_full_matrix_campaign_readiness(
                config=enabled_config(),
                inputs=PhysicalFullMatrixCampaignInputs(),
                now=NOW,
            )
            self.assertIs(
                positive,
                readiness.require_verified_physical_full_matrix_campaign_readiness(
                    verified,
                    now=NOW + timedelta(seconds=1),
                ),
            )
        self.assertEqual(2, assessor.call_count)
        with self.assertRaisesRegex(TypeError, "SERIALIZATION_FORBIDDEN"):
            pickle.dumps(verified)

        # A mint cannot turn the normal current blocked posture into a
        # capability.  This call uses the real assessor, rather than a test
        # fixture, to preserve the V1 activation fence.
        with self.assertRaisesRegex(
            PhysicalFullMatrixCampaignReadinessError,
            "POSITIVE_REQUIRED",
        ):
            readiness.mint_verified_physical_full_matrix_campaign_readiness(
                config=enabled_config(),
                inputs=PhysicalFullMatrixCampaignInputs(),
                now=NOW,
            )

        # Even a legitimately minted process-local wrapper is re-assessed at
        # the execution clock.  A now-blocked result cannot reuse the earlier
        # positive report.
        with patch.object(
            readiness,
            "assess_physical_full_matrix_campaign_readiness",
            side_effect=(positive, blocked),
        ):
            rechecked = readiness.mint_verified_physical_full_matrix_campaign_readiness(
                config=enabled_config(),
                inputs=PhysicalFullMatrixCampaignInputs(),
                now=NOW,
            )
            with self.assertRaisesRegex(
                PhysicalFullMatrixCampaignReadinessError,
                "REVALIDATION_BLOCKED",
            ):
                readiness.require_verified_physical_full_matrix_campaign_readiness(
                    rechecked,
                    now=NOW + timedelta(seconds=1),
                )

    def test_reverse_direction_binding_is_admitted_without_coercion(self) -> None:
        """Failback evidence must remain IR -> FI, never normalize to FI -> IR."""

        reverse = replace(
            binding(),
            source_site="webapp_ir",
            destination_site="webapp_fi",
            baseline_generation_id="failback-baseline-20260731",
            stream_generation_id="failback-stream-20260731",
            route_binding_sha256="2" * 64,
            writer_epoch=8,
            writer_lease_id="lease-20260731-ir",
            witness_transition_id="transition-20260731-ir",
            witnessed_term_proof_sha256="3" * 64,
            recovery_stage_bundle_id="4" * 64,
            recovery_stage_receipt_sha256="5" * 64,
        )
        result = readiness.assess_physical_full_matrix_campaign_readiness(
            PhysicalFullMatrixCampaignReadinessConfig(binding=reverse, enabled=True),
            PhysicalFullMatrixCampaignInputs(),
            now=NOW,
        )
        self.assertEqual(PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED, result.status)
        self.assertNotEqual(
            readiness._normalise_binding(binding()).binding_sha256,
            result.binding_sha256,
        )
        self.assertEqual(
            readiness._normalise_binding(reverse).binding_sha256,
            result.binding_sha256,
        )
        self.assertIn("missing-physical-wal-bundle", result.reason_codes)

        invalid = replace(reverse, destination_site="webapp_ir")
        rejected = readiness.assess_physical_full_matrix_campaign_readiness(
            PhysicalFullMatrixCampaignReadinessConfig(binding=invalid, enabled=True),
            PhysicalFullMatrixCampaignInputs(),
            now=NOW,
        )
        self.assertEqual(("invalid-campaign-binding",), rejected.reason_codes)

    def test_default_off_and_empty_enabled_inputs_are_deterministic(self) -> None:
        disabled = readiness.assess_physical_full_matrix_campaign_readiness(
            PhysicalFullMatrixCampaignReadinessConfig(binding=binding()),
            PhysicalFullMatrixCampaignInputs(),
            now=NOW,
        )
        self.assertEqual(disabled.status, PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED)
        self.assertEqual(disabled.reason_codes, ("driver-disabled",))

        result = readiness.assess_physical_full_matrix_campaign_readiness(
            enabled_config(),
            PhysicalFullMatrixCampaignInputs(),
            now=NOW,
        )
        self.assertEqual(result.status, PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED)
        self.assertEqual(
            result.reason_codes,
            (
                "missing-physical-wal-recovery-observation",
                "missing-physical-wal-bundle",
                "missing-remote-ack-evidence",
                "missing-remote-ack-receiver-recovery",
                "missing-remote-ack-durable-ledger",
                "missing-strict-remote-ack-writer-response",
                "missing-four-role-arvan-object-storage-immutability-preflight",
                "missing-arvan-object-storage-failback-preflight",
                "missing-blob-promotion-evidence",
                "missing-current-witness-term",
                "missing-current-role-activation",
                "missing-deployment-preflight-posture",
                "missing-p0-auth-upload-result",
                "missing-external-effect-reconciliation-decision",
                "missing-source-write-fence-recovery-route",
                "missing-v2-chunked-recovery-evidence",
            ),
        )
        self.assertFalse(result.external_execution_authorized)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.execution_authorized)

    def test_legacy_runner_artifacts_are_rejected_without_evidence_inspection(self) -> None:
        result = readiness.assess_physical_full_matrix_campaign_readiness(
            enabled_config(),
            PhysicalFullMatrixCampaignInputs(
                recovery_observation=object(),
                legacy_runner_artifacts={"schema": "production_full_matrix_runner_plan_v1"},
            ),
            now=NOW,
        )
        self.assertEqual(result.reason_codes, ("legacy-runner-artifact-rejected",))
        self.assertEqual(result.observed_slots, ())

    def test_bad_typed_evidence_becomes_a_mismatch_not_an_exception(self) -> None:
        result = readiness.assess_physical_full_matrix_campaign_readiness(
            enabled_config(),
            PhysicalFullMatrixCampaignInputs(recovery_observation=object()),
            now=NOW,
        )
        self.assertIn("physical-wal-recovery-observation-mismatch", result.reason_codes)
        self.assertIn("missing-physical-wal-bundle", result.reason_codes)
        self.assertEqual(result.status, PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED)

    def test_source_fence_and_strict_writer_response_are_opaque_and_fail_closed(self) -> None:
        verified_fence = verify_physical_full_matrix_source_fence_recovery_route(
            source_fence_observation(),
            binding=binding(),
            now=NOW,
        )
        self.assertIs(
            require_verified_physical_full_matrix_source_fence_recovery_route(
                verified_fence,
                binding=binding(),
                now=NOW,
            ),
            verified_fence,
        )
        with self.assertRaises(PhysicalFullMatrixCampaignReadinessError):
            verify_physical_full_matrix_source_fence_recovery_route(
                source_fence_observation(direct_fi_to_ir_control="permitted"),
                binding=binding(),
                now=NOW,
            )

        verified_response = verify_physical_full_matrix_strict_remote_ack_writer_response(
            strict_writer_response(),
            binding=binding(),
            now=NOW,
        )
        self.assertIs(
            require_verified_physical_full_matrix_strict_remote_ack_writer_response(
                verified_response,
                binding=binding(),
                now=NOW,
            ),
            verified_response,
        )
        # A legacy boolean-shaped wrapper is retained only for compatibility
        # diagnostics.  It is no longer an admissible readiness-oracle input:
        # only the owning strict boundary can mint that opaque observation.
        with self.assertRaises(PhysicalFullMatrixCampaignReadinessError):
            readiness._expect_verified_strict_writer_response(
                verified_response,
                binding=binding(),
                now=NOW,
                maximum_evidence_age_seconds=90,
            )
        with self.assertRaises(PhysicalFullMatrixCampaignReadinessError):
            verify_physical_full_matrix_strict_remote_ack_writer_response(
                strict_writer_response(durable_commit_coupled=False),
                binding=binding(),
                now=NOW,
            )

    def test_deployment_and_external_effect_inputs_recheck_their_own_boundaries(self) -> None:
        posture = deployment_posture()
        verified_posture = verify_physical_full_matrix_deployment_preflight_posture(
            posture,
            binding=binding(),
            now=NOW,
        )
        self.assertIs(
            require_verified_physical_full_matrix_deployment_preflight_posture(
                verified_posture,
                binding=binding(),
                now=NOW,
            ),
            verified_posture,
        )
        bad_posture = deployment_posture()
        bad_posture.receipts[1]["observation"]["runtime"]["current_link_present"] = False
        with self.assertRaises(PhysicalFullMatrixCampaignReadinessError):
            verify_physical_full_matrix_deployment_preflight_posture(
                bad_posture,
                binding=binding(),
                now=NOW,
            )

        verified_external = verify_physical_full_matrix_external_effect_reconciliation(
            external_effect_authorization(),
            binding=binding(),
            now=NOW,
        )
        self.assertIs(
            require_verified_physical_full_matrix_external_effect_reconciliation(
                verified_external,
                binding=binding(),
                now=NOW,
            ),
            verified_external,
        )
        with self.assertRaises(PhysicalFullMatrixCampaignReadinessError):
            verify_physical_full_matrix_external_effect_reconciliation(
                external_effect_authorization(holder_site="webapp_ir"),
                binding=binding(),
                now=NOW,
            )

    def test_legacy_normal_immutability_preflight_is_rejected_for_full_matrix(self) -> None:
        verified = arvan_immutability_preflight()
        readiness._expect_verified_arvan_immutability_preflight(
            verified,
            preflight_binding=arvan_immutability_binding(),
            binding=binding(),
            now=NOW,
            maximum_evidence_age_seconds=90,
        )
        with self.assertRaises(PhysicalFullMatrixCampaignReadinessError):
            readiness._expect_verified_arvan_immutability_preflight(
                verified,
                preflight_binding=arvan_immutability_binding(),
                binding=replace(binding(), route_binding_sha256="0" * 64),
                now=NOW,
                maximum_evidence_age_seconds=90,
            )
        # Supplying a valid-looking replacement does not rehabilitate the
        # historical normal-only input.  The new slot may be observed, but
        # the old two-role artifact remains an independent fail-closed reason.
        with patch.object(readiness, "_expect_verified_arvan_four_role_immutability_preflight"):
            mismatch = readiness.assess_physical_full_matrix_campaign_readiness(
                enabled_config(),
                PhysicalFullMatrixCampaignInputs(
                    arvan_immutability_preflight=verified,
                    arvan_immutability_preflight_binding=None,
                    arvan_four_role_immutability_preflight=object(),
                    arvan_four_role_immutability_preflight_config=object(),
                    arvan_four_role_immutability_live_iam_durable_admission=object(),
                    arvan_four_role_immutability_live_iam_binding=object(),
                    arvan_four_role_immutability_failback_binding=object(),
                ),
                now=NOW,
            )
        self.assertIn(
            "legacy-arvan-object-storage-immutability-preflight-rejected",
            mismatch.reason_codes,
        )
        self.assertIn(
            "four-role-arvan-object-storage-immutability-preflight",
            mismatch.observed_slots,
        )

    def test_four_role_immutability_requires_all_opaque_owning_inputs(self) -> None:
        verified, configuration, live_iam, reverse_binding = (
            arvan_four_role_immutability_preflight()
        )
        readiness._expect_verified_arvan_four_role_immutability_preflight(
            verified,
            preflight_config=configuration,
            live_iam_durable_admission=live_iam.live_iam_durable_admission,
            live_iam_binding=live_iam.live_iam_binding,
            failback_binding=reverse_binding,
            binding=binding(),
            now=NOW,
            maximum_evidence_age_seconds=90,
        )
        # The contract may have a broader local freshness cap, but readiness
        # applies its own campaign cap again before accepting the slot.
        with self.assertRaises(PhysicalFullMatrixCampaignReadinessError):
            readiness._expect_verified_arvan_four_role_immutability_preflight(
                verified,
                preflight_config=replace(
                    configuration,
                    maximum_evidence_age_seconds=300,
                ),
                live_iam_durable_admission=live_iam.live_iam_durable_admission,
                live_iam_binding=live_iam.live_iam_binding,
                failback_binding=reverse_binding,
                binding=binding(),
                now=NOW + timedelta(seconds=91),
                maximum_evidence_age_seconds=90,
            )
        # The explicitly injected durable-admission field rejects a raw gate;
        # a gate is not a durable receipt.  A verified instance of the
        # retired normal-only contract is likewise not a four-role proof.
        with self.assertRaises(PhysicalFullMatrixCampaignReadinessError):
            readiness._expect_verified_arvan_four_role_immutability_preflight(
                verified,
                preflight_config=configuration,
                live_iam_durable_admission=live_iam.live_iam_durable_admission.gate,
                live_iam_binding=live_iam.live_iam_binding,
                failback_binding=reverse_binding,
                binding=binding(),
                now=NOW,
                maximum_evidence_age_seconds=90,
            )
        with self.assertRaises(PhysicalFullMatrixCampaignReadinessError):
            readiness._expect_verified_arvan_four_role_immutability_preflight(
                arvan_immutability_preflight(),
                preflight_config=configuration,
                live_iam_durable_admission=live_iam.live_iam_durable_admission,
                live_iam_binding=live_iam.live_iam_binding,
                failback_binding=reverse_binding,
                binding=binding(),
                now=NOW,
                maximum_evidence_age_seconds=90,
            )

        incomplete = readiness.assess_physical_full_matrix_campaign_readiness(
            enabled_config(),
            PhysicalFullMatrixCampaignInputs(
                arvan_four_role_immutability_preflight=verified,
                arvan_four_role_immutability_preflight_config=configuration,
            ),
            now=NOW,
        )
        self.assertIn(
            "four-role-arvan-object-storage-immutability-preflight-mismatch",
            incomplete.reason_codes,
        )
        self.assertNotIn(
            "four-role-arvan-object-storage-immutability-preflight",
            incomplete.observed_slots,
        )

    def test_four_role_immutability_real_contract_rejects_route_mismatch(self) -> None:
        verified, configuration, live_iam, reverse_binding = (
            arvan_four_role_immutability_preflight()
        )
        wrong_route = replace(
            reverse_binding,
            normal_route_scope_sha256="e" * 64,
        )
        with self.assertRaises(PhysicalFullMatrixCampaignReadinessError):
            readiness._expect_verified_arvan_four_role_immutability_preflight(
                verified,
                preflight_config=configuration,
                live_iam_durable_admission=live_iam.live_iam_durable_admission,
                live_iam_binding=live_iam.live_iam_binding,
                failback_binding=wrong_route,
                binding=binding(),
                now=NOW,
                maximum_evidence_age_seconds=90,
            )

    def test_four_identity_failback_preflight_is_mandatory_and_cross_directional(self) -> None:
        reverse_binding = arvan_failback_binding()
        verified, fixture, live_iam = arvan_failback_preflight(
            reverse_binding,
            include_context=True,
        )
        configuration = fixture.preflight_config(
            four_role_live_iam_binding=live_iam.live_iam_binding,
            four_role_live_iam_durable_admission=live_iam.live_iam_durable_admission,
        )
        readiness._expect_verified_arvan_failback_preflight(
            verified,
            preflight_config=configuration,
            binding=binding(),
            now=NOW,
        )
        with self.assertRaises(PhysicalFullMatrixCampaignReadinessError):
            readiness._expect_verified_arvan_failback_preflight(
                verified,
                preflight_config=configuration,
                binding=replace(binding(), release_sha="b" * 40),
                now=NOW,
            )
        collision = arvan_failback_binding(fi_receiver_identity_sha256="7" * 64)
        with self.assertRaises(Exception):
            arvan_failback_preflight(collision)
        mismatch = readiness.assess_physical_full_matrix_campaign_readiness(
            enabled_config(),
            PhysicalFullMatrixCampaignInputs(
                arvan_failback_preflight=verified,
                arvan_failback_preflight_config=None,
            ),
            now=NOW,
        )
        self.assertIn("arvan-object-storage-failback-preflight-mismatch", mismatch.reason_codes)

    def test_all_injected_observations_remain_explicitly_non_authorizing(self) -> None:
        inputs = PhysicalFullMatrixCampaignInputs(
            recovery_observation=object(),
            v2_recovery_evidence=object(),
            physical_wal_bundle=object(),
            remote_ack_evidence=object(),
            remote_ack_receiver_recovery=object(),
            remote_ack_durable_ledger=object(),
            strict_remote_ack_writer_response=object(),
            arvan_four_role_immutability_preflight=object(),
            arvan_four_role_immutability_preflight_config=object(),
            arvan_four_role_immutability_live_iam_durable_admission=object(),
            arvan_four_role_immutability_live_iam_binding=object(),
            arvan_four_role_immutability_failback_binding=object(),
            arvan_failback_preflight=object(),
            arvan_failback_preflight_config=object(),
            blob_promotion_evidence=object(),
            blob_storage_binding=object(),
            blob_promotion_config=object(),
            witnessed_term=object(),
            role_activation=object(),
            deployment_preflight_posture=object(),
            p0_auth_upload_result=object(),
            external_effect_reconciliation=object(),
            source_fence_recovery_route=object(),
        )
        with (
            patch.object(readiness, "_bundle_facts", return_value=object()),
            patch.object(readiness, "_v2_chunked_recovery_evidence_facts"),
            patch.object(readiness, "_recovery_observation_facts"),
            patch.object(readiness, "_remote_ack_facts", return_value=(object(), object())),
            patch.object(readiness, "_remote_ack_receiver_recovery_facts", return_value=object()),
            patch.object(readiness, "_remote_ack_durable_ledger_facts"),
            patch.object(readiness, "_expect_verified_strict_writer_response"),
            patch.object(
                readiness,
                "_expect_verified_arvan_four_role_immutability_preflight",
            ) as four_role_expect,
            patch.object(
                readiness,
                "_expect_verified_arvan_failback_preflight",
                return_value=inputs.arvan_four_role_immutability_failback_binding,
            ),
            patch.object(readiness, "_blob_promotion_facts"),
            patch.object(readiness, "_witness_term_facts"),
            patch.object(readiness, "_role_activation_facts"),
            patch.object(readiness, "_expect_verified_deployment"),
            patch.object(readiness, "_p0_auth_upload_facts"),
            patch.object(readiness, "_expect_verified_external_effect_reconciliation"),
            patch.object(readiness, "_expect_verified_source_fence_route"),
        ):
            result = readiness.assess_physical_full_matrix_campaign_readiness(
                enabled_config(),
                inputs,
                now=NOW,
            )
        # The independent V1 bundle fence is deliberately still blocking;
        # this test proves the new owning-boundary slot itself is positive and
        # nevertheless grants no execution, promotion, or external authority.
        self.assertEqual(result.status, "blocked")
        self.assertEqual(
            result.reason_codes,
            (
                "v1-single-object-base-backup-activation-fenced",
                "v2-strict-remote-ack-chain-not-integrated",
            ),
        )
        self.assertIn(
            "four-role-arvan-object-storage-immutability-preflight",
            result.observed_slots,
        )
        four_role_expect.assert_called_once()
        self.assertFalse(result.external_execution_authorized)
        self.assertFalse(result.promotion_authorized)
        self.assertFalse(result.execution_authorized)

    def test_v1_single_object_bundle_cannot_become_positive_readiness(self) -> None:
        """Even a future-valid-looking legacy bundle remains an activation blocker."""

        legacy_bundle = object()
        with patch.object(readiness, "_bundle_facts", return_value=legacy_bundle):
            result = readiness.assess_physical_full_matrix_campaign_readiness(
                enabled_config(),
                PhysicalFullMatrixCampaignInputs(physical_wal_bundle=legacy_bundle),
                now=NOW,
            )
        self.assertEqual(PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED, result.status)
        self.assertIn(
            readiness.PHYSICAL_FULL_MATRIX_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_FENCE_REASON,
            result.reason_codes,
        )
        self.assertNotIn("physical-wal-bundle", result.observed_slots)

    def test_valid_v2_recovery_slot_is_observed_but_v2_ack_and_v1_remain_fenced(self) -> None:
        """A real V2 bridge is visibility only until V2 ACK replaces V1 atomically."""

        fixture = _V2RecoveryEvidenceFixture(
            "test_revalidates_full_v2_recovery_and_coverage_chain"
        )
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        evidence = fixture.mint()
        transfer = evidence.transfer_binding
        v2_binding = PhysicalFullMatrixCampaignBinding(
            campaign_id=transfer.campaign_id,
            release_sha=transfer.release_sha,
            schema_revision="alembic-v2-recovery-20260731",
            source_site=transfer.source_site,
            destination_site=transfer.destination_site,
            baseline_generation_id=evidence.baseline_generation_id,
            baseline_manifest_sha256=evidence.manifest_sha256,
            baseline_wal_lsn=evidence.baseline_wal_lsn,
            timeline_id=evidence.timeline_id,
            stream_generation_id=evidence.stream_generation_id,
            destination_age_recipient=transfer.destination_age_recipient,
            route_binding_sha256=transfer.route_commitment_sha256,
            writer_epoch=transfer.writer_term.writer_epoch,
            writer_lease_id=transfer.writer_term.writer_lease_id,
            witness_transition_id=evidence.witness_transition_id,
            witnessed_term_proof_sha256=transfer.writer_term.witnessed_term_proof_sha256,
            target_acknowledged_wal_lsn=evidence.target_replay_lsn,
            # The V2 bridge proves coverage at one exact target only.
            blob_object_frontier_wal_lsn=evidence.target_replay_lsn,
            recovery_stage_bundle_id="e" * 64,
            recovery_stage_receipt_sha256=evidence.stage_receipt_sha256,
            deployment_operation_id=DEPLOYMENT_OPERATION_ID,
            deployment_manifest_sha256="1" * 64,
            p0_operation_id=P0_OPERATION_ID,
        )
        config = PhysicalFullMatrixCampaignReadinessConfig(
            binding=v2_binding,
            enabled=True,
        )

        missing_v1 = readiness.assess_physical_full_matrix_campaign_readiness(
            config,
            PhysicalFullMatrixCampaignInputs(v2_recovery_evidence=evidence),
            now=NOW,
        )
        self.assertEqual(PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED, missing_v1.status)
        self.assertIn(
            readiness.PHYSICAL_FULL_MATRIX_V2_CHUNKED_RECOVERY_EVIDENCE_SLOT,
            missing_v1.observed_slots,
        )
        self.assertIn("missing-physical-wal-bundle", missing_v1.reason_codes)
        self.assertIn(
            readiness.PHYSICAL_FULL_MATRIX_V2_STRICT_REMOTE_ACK_CHAIN_FENCE_REASON,
            missing_v1.reason_codes,
        )
        self.assertNotIn(
            readiness.PHYSICAL_FULL_MATRIX_V2_CHUNKED_RECOVERY_EVIDENCE_MISSING_REASON,
            missing_v1.reason_codes,
        )
        self.assertFalse(missing_v1.external_execution_authorized)
        self.assertFalse(missing_v1.promotion_authorized)
        self.assertFalse(missing_v1.execution_authorized)

        # Supplying even a legacy-looking bundle cannot erase either fence;
        # it merely changes the V1 diagnostic from missing to explicitly
        # retired/fenced.  The real V2 evidence remains observed.
        with_legacy_v1 = readiness.assess_physical_full_matrix_campaign_readiness(
            config,
            PhysicalFullMatrixCampaignInputs(
                v2_recovery_evidence=evidence,
                physical_wal_bundle=object(),
            ),
            now=NOW,
        )
        self.assertEqual(PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED, with_legacy_v1.status)
        self.assertIn(
            readiness.PHYSICAL_FULL_MATRIX_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_FENCE_REASON,
            with_legacy_v1.reason_codes,
        )
        self.assertIn(
            readiness.PHYSICAL_FULL_MATRIX_V2_STRICT_REMOTE_ACK_CHAIN_FENCE_REASON,
            with_legacy_v1.reason_codes,
        )
        self.assertIn(
            readiness.PHYSICAL_FULL_MATRIX_V2_CHUNKED_RECOVERY_EVIDENCE_SLOT,
            with_legacy_v1.observed_slots,
        )

    def test_cli_is_a_non_deserializing_blocked_boundary(self) -> None:
        script_path = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "assess_physical_full_matrix_campaign_readiness.py"
        )
        spec = importlib.util.spec_from_file_location("physical_readiness_cli", script_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(
            module.report(legacy_runner_artifacts=[])["reason_codes"],
            ["typed-injected-evidence-required"],
        )
        self.assertEqual(
            module.report(legacy_runner_artifacts=["production_full_matrix_runner_plan_v1"])[
                "reason_codes"
            ],
            ["legacy-runner-artifact-rejected"],
        )


if __name__ == "__main__":
    unittest.main()
