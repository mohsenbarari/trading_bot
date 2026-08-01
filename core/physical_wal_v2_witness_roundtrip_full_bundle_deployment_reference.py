"""Pure V2 bridge from signed FullBundleAttestation to deployment-plan pins.

The deployment-plan renderer intentionally knows only a public, immutable
reference.  This boundary is the sole narrow conversion from a canonical,
fresh, locally verified eight-role FullBundleAttestation into that reference.
It performs no installation, runtime opening, filesystem access, network I/O,
or credential handling.  In particular, it does not accept a caller-built
mapping or an unverified typed lookalike.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_delivery_dispatcher as _dispatcher
from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_scope as _scope
from core import physical_wal_v2_witness_roundtrip_deployment_plan as _deployment


__all__ = (
    "PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError",
    "derive_physical_wal_v2_witness_roundtrip_public_full_bundle_reference",
    "require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_with_fresh_full_bundle_admission",
    "require_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest_with_fresh_full_bundle_admission",
    "require_physical_wal_v2_witness_roundtrip_witness_service_manifest_with_fresh_full_bundle_admission",
)


class PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError(ValueError):
    """A signed full-bundle proof cannot safely become a deployment pin."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError(code)


def _local_projection_cross_pin(
    *,
    verified: _dispatcher.VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestation,
    verification_config: _dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig,
) -> tuple[str, str, str]:
    """Bind the signed projection to the one local admission used to verify it.

    The dispatcher verifier has already freshly validated these typed local
    objects.  This extra comparison prevents a correctly signed but
    cross-host/cross-role projection from becoming a deployment reference
    before the later dispatcher-open cross-pin stage exists.
    """

    try:
        local_config = verification_config.mailbox_adapter_config
        local_admission = local_config.mailbox_admission
        local_retention = local_config.retention_proof
        local_role = local_admission.local_role
        matching = tuple(
            projection
            for projection in verified.projections
            if projection.local_role == local_role
        )
        if len(matching) != 1:
            _fail(
                "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_LOCAL_CROSS_PIN_MISMATCH"
            )
        projection = matching[0]
        if (
            projection.host_id != local_admission.host_id
            or projection.mailbox != local_admission.mailbox
            or projection.direction != local_admission.direction
            or projection.object_prefix != local_admission.object_prefix
            or projection.admission_sha256 != local_admission.admission_sha256
            or projection.deployment_binding_sha256
            != local_admission.deployment_binding_sha256
            or projection.delivery_binding_sha256
            != local_admission.delivery_binding_sha256
            or projection.retention_proof_sha256 != local_retention.proof_sha256
        ):
            _fail(
                "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_LOCAL_CROSS_PIN_MISMATCH"
            )
        return (
            local_role,
            local_admission.direction,
            projection.provider_route_iam_attestation_sha256,
        )
    except PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError:
        raise
    except AttributeError as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_LOCAL_CROSS_PIN_MISMATCH"
        ) from exc


def _verified_public_full_bundle_reference(
    full_bundle_attestation: bytes,
    *,
    verification_config: _dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig,
    now: datetime,
) -> tuple[
    _deployment.PhysicalWalV2WitnessRoundtripPublicFullBundleReference,
    str,
    str,
    str,
]:
    """Return fresh public pins plus the one locally verified role.

    The verifier is the same fixed-topology verifier used by the named local
    dispatchers.  It derives its authority and deployment binding solely from
    the supplied local mailbox admission, verifies the signature, and checks
    all eight exact role projections before this function exposes any plan
    reference.
    """

    try:
        verified = (
            _dispatcher.verify_physical_wal_v2_witness_roundtrip_arvan_s3v4_full_bundle_attestation(
                full_bundle_attestation,
                config=verification_config,
                now=now,
            )
        )
    except _dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_FULL_BUNDLE_INVALID"
        ) from exc

    try:
        (
            fi_writer_source_outbox,
            witness_fi_ingress,
            witness_ir_egress,
            ir_standby_ack_inbox,
            ir_durable_ack_outbox,
            witness_ir_ingress,
            witness_fi_egress,
            fi_writer_ack_inbox,
        ) = verified.projections
    except (AttributeError, TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_TOPOLOGY_INVALID"
        ) from exc

    expected_roles = (
        "fi-writer-source-outbox",
        "witness-fi-ingress",
        "witness-ir-egress",
        "ir-standby-ack-inbox",
        "ir-durable-ack-outbox",
        "witness-ir-ingress",
        "witness-fi-egress",
        "fi-writer-ack-inbox",
    )
    projections = (
        fi_writer_source_outbox,
        witness_fi_ingress,
        witness_ir_egress,
        ir_standby_ack_inbox,
        ir_durable_ack_outbox,
        witness_ir_ingress,
        witness_fi_egress,
        fi_writer_ack_inbox,
    )
    try:
        roundtrip_configuration_sha256 = (
            fi_writer_source_outbox.roundtrip_configuration_sha256
        )
        if (
            tuple(projection.local_role for projection in projections) != expected_roles
            or any(
                projection.deployment_binding_sha256
                != verified.deployment_binding_sha256
                or projection.roundtrip_configuration_sha256
                != roundtrip_configuration_sha256
                for projection in projections
            )
        ):
            _fail(
                "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_TOPOLOGY_INVALID"
            )
    except AttributeError as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_TOPOLOGY_INVALID"
        ) from exc

    local_role, local_direction, local_route_iam_attestation_sha256 = (
        _local_projection_cross_pin(
            verified=verified,
            verification_config=verification_config,
        )
    )

    return (
        _deployment.PhysicalWalV2WitnessRoundtripPublicFullBundleReference(
            bundle_id=verified.bundle_id,
            release_sha=verified.release_sha,
            full_bundle_attestation_sha256=verified.attestation_sha256,
            deployment_binding_sha256=verified.deployment_binding_sha256,
            deployment_authority_public_key_sha256=(
                verified.deployment_authority_public_key_sha256
            ),
            roundtrip_configuration_sha256=roundtrip_configuration_sha256,
        ),
        local_role,
        local_direction,
        local_route_iam_attestation_sha256,
    )


def derive_physical_wal_v2_witness_roundtrip_public_full_bundle_reference(
    full_bundle_attestation: bytes,
    *,
    verification_config: _dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig,
    now: datetime,
) -> _deployment.PhysicalWalV2WitnessRoundtripPublicFullBundleReference:
    """Verify one fresh signed wire bundle, then derive its public plan pins."""

    reference, _local_role, _local_direction, _local_route_iam_attestation_sha256 = (
        _verified_public_full_bundle_reference(
            full_bundle_attestation,
            verification_config=verification_config,
            now=now,
        )
    )
    return reference


def _require_named_manifest_with_fresh_full_bundle(
    manifest: bytes,
    *,
    manifest_admission_config: _deployment.PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig,
    full_bundle_attestation: bytes,
    verification_config: _dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig,
    provider_route_iam_attestation_config: _scope.PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig,
    provider_route_iam_attestation: _scope.VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation,
    now: datetime,
    expected_local_roles: tuple[str, ...],
    admission: Callable[
        [bytes], _deployment.PhysicalWalV2WitnessRoundtripServiceManifest
    ],
) -> _deployment.PhysicalWalV2WitnessRoundtripServiceManifest:
    """Freshly bind one exact local manifest to public bundle and route pins."""

    (
        reference,
        local_role,
        local_direction,
        expected_route_iam_sha256,
    ) = _verified_public_full_bundle_reference(
        full_bundle_attestation,
        verification_config=verification_config,
        now=now,
    )
    try:
        local_config = verification_config.mailbox_adapter_config
        if (
            provider_route_iam_attestation_config.mailbox_adapter_config
            is not local_config
        ):
            _fail(
                "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_LOCAL_ROUTE_IAM_CONFIG_IDENTITY_MISMATCH"
            )
        local_route_iam = (
            _scope.require_verified_physical_wal_v2_witness_roundtrip_arvan_s3v4_provider_route_iam_attestation(
                provider_route_iam_attestation,
                config=provider_route_iam_attestation_config,
                local_role=local_role,
                direction=local_direction,
                now=now,
            )
        )
    except PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError:
        raise
    except (
        AttributeError,
        _scope.PhysicalWalV2WitnessRoundtripArvanS3v4ScopeError,
    ) as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_LOCAL_ROUTE_IAM_INVALID"
        ) from exc
    if local_route_iam.attestation_sha256 != expected_route_iam_sha256:
        _fail(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_LOCAL_ROUTE_IAM_CROSS_PIN_MISMATCH"
        )
    if local_role not in expected_local_roles:
        _fail(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_MANIFEST_SITE_LOCAL_ROLE_MISMATCH"
        )
    try:
        parsed = admission(manifest)
    except _deployment.PhysicalWalV2WitnessRoundtripDeploymentPlanError as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleDeploymentReferenceError(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_MANIFEST_ADMISSION_INVALID"
        ) from exc
    if parsed.full_bundle_reference != reference:
        _fail(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_DEPLOYMENT_REFERENCE_MANIFEST_FULL_BUNDLE_CROSS_PIN_MISMATCH"
        )
    return parsed


def require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_with_fresh_full_bundle_admission(
    manifest: bytes,
    *,
    manifest_admission_config: _deployment.PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig,
    full_bundle_attestation: bytes,
    verification_config: _dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig,
    provider_route_iam_attestation_config: _scope.PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig,
    provider_route_iam_attestation: _scope.VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation,
    now: datetime,
) -> _deployment.PhysicalWalV2WitnessRoundtripServiceManifest:
    """Admit WA-FI only with its own fresh signed eight-role bundle."""

    return _require_named_manifest_with_fresh_full_bundle(
        manifest,
        manifest_admission_config=manifest_admission_config,
        full_bundle_attestation=full_bundle_attestation,
        verification_config=verification_config,
        provider_route_iam_attestation_config=provider_route_iam_attestation_config,
        provider_route_iam_attestation=provider_route_iam_attestation,
        now=now,
        expected_local_roles=("fi-writer-source-outbox", "fi-writer-ack-inbox"),
        admission=lambda value: (
            _deployment.require_physical_wal_v2_witness_roundtrip_wa_fi_service_manifest_admission(
                value,
                config=manifest_admission_config,
            )
        ),
    )


def require_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest_with_fresh_full_bundle_admission(
    manifest: bytes,
    *,
    manifest_admission_config: _deployment.PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig,
    full_bundle_attestation: bytes,
    verification_config: _dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig,
    provider_route_iam_attestation_config: _scope.PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig,
    provider_route_iam_attestation: _scope.VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation,
    now: datetime,
) -> _deployment.PhysicalWalV2WitnessRoundtripServiceManifest:
    """Admit WA-IR only with its own fresh signed eight-role bundle."""

    return _require_named_manifest_with_fresh_full_bundle(
        manifest,
        manifest_admission_config=manifest_admission_config,
        full_bundle_attestation=full_bundle_attestation,
        verification_config=verification_config,
        provider_route_iam_attestation_config=provider_route_iam_attestation_config,
        provider_route_iam_attestation=provider_route_iam_attestation,
        now=now,
        expected_local_roles=("ir-standby-ack-inbox", "ir-durable-ack-outbox"),
        admission=lambda value: (
            _deployment.require_physical_wal_v2_witness_roundtrip_wa_ir_service_manifest_admission(
                value,
                config=manifest_admission_config,
            )
        ),
    )


def require_physical_wal_v2_witness_roundtrip_witness_service_manifest_with_fresh_full_bundle_admission(
    manifest: bytes,
    *,
    manifest_admission_config: _deployment.PhysicalWalV2WitnessRoundtripServiceManifestAdmissionConfig,
    full_bundle_attestation: bytes,
    verification_config: _dispatcher.PhysicalWalV2WitnessRoundtripArvanS3v4FullBundleAttestationConfig,
    provider_route_iam_attestation_config: _scope.PhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestationConfig,
    provider_route_iam_attestation: _scope.VerifiedPhysicalWalV2WitnessRoundtripArvanS3v4ProviderRouteIamAttestation,
    now: datetime,
) -> _deployment.PhysicalWalV2WitnessRoundtripServiceManifest:
    """Admit Witness only with its own fresh signed eight-role bundle."""

    return _require_named_manifest_with_fresh_full_bundle(
        manifest,
        manifest_admission_config=manifest_admission_config,
        full_bundle_attestation=full_bundle_attestation,
        verification_config=verification_config,
        provider_route_iam_attestation_config=provider_route_iam_attestation_config,
        provider_route_iam_attestation=provider_route_iam_attestation,
        now=now,
        expected_local_roles=(
            "witness-fi-ingress",
            "witness-ir-egress",
            "witness-ir-ingress",
            "witness-fi-egress",
        ),
        admission=lambda value: (
            _deployment.require_physical_wal_v2_witness_roundtrip_witness_service_manifest_admission(
                value,
                config=manifest_admission_config,
            )
        ),
    )
