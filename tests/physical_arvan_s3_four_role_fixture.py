"""Small pure fixtures for consumers of the mandatory four-role binder."""

from __future__ import annotations

from dataclasses import dataclass

from core import physical_arvan_s3_four_role_preflight_binding as binder
from core import physical_arvan_s3_role_profiles as profiles
from core import physical_ir_to_fi_object_storage_failback_preflight as preflight
from core.physical_arvan_s3_role_local_identity import (
    PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
    ArvanS3RoleLocalIdentityProjection,
)
from core.physical_arvan_s3_role_local_route_policy import ArvanS3RoleLocalRoutePolicy
from core.physical_wal_object_manifest import (
    PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
)


@dataclass(frozen=True)
class FourRoleFixture:
    binding: preflight.PhysicalIrToFiObjectStorageFailbackBinding
    verified_binding: binder.VerifiedPhysicalArvanS3FourRolePreflightBinding
    normal_route_policy: ArvanS3RoleLocalRoutePolicy
    reverse_route_policy: ArvanS3RoleLocalRoutePolicy

    def preflight_config(
        self,
        *,
        enabled: bool = True,
        maximum_evidence_age_seconds: int = 120,
        four_role_live_iam_binding: object | None = None,
        four_role_live_iam_durable_admission: object | None = None,
    ) -> preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig:
        return preflight.PhysicalIrToFiObjectStorageFailbackPreflightConfig(
            binding=self.binding,
            enabled=enabled,
            maximum_evidence_age_seconds=maximum_evidence_age_seconds,
            four_role_projection_binding=self.verified_binding,
            four_role_live_iam_binding=four_role_live_iam_binding,
            four_role_live_iam_durable_admission=four_role_live_iam_durable_admission,
        )


def make_four_role_fixture(
    *,
    campaign_id: str,
    release_sha: str,
    fi_publisher_identity_sha256: str,
    ir_receiver_identity_sha256: str,
    ir_publisher_identity_sha256: str,
    fi_receiver_identity_sha256: str,
    endpoint: str = "https://s3.ir-thr-at1.arvanstorage.ir",
    region: str = "ir-thr-at1",
    normal_bucket: str = "private-physical-recovery",
    reverse_bucket: str = "private-physical-failback",
) -> FourRoleFixture:
    """Build only test-local public projection facts; no files or I/O occur."""

    normal_route_policy = ArvanS3RoleLocalRoutePolicy(
        endpoint=endpoint,
        region=region,
        bucket=normal_bucket,
        enabled=True,
        source_site="webapp_fi",
        destination_site="webapp_ir",
        object_storage_namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
    )
    reverse_route_policy = ArvanS3RoleLocalRoutePolicy(
        endpoint=endpoint,
        region=region,
        bucket=reverse_bucket,
        enabled=True,
        source_site="webapp_ir",
        destination_site="webapp_fi",
        object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
    )
    fi_publisher = ArvanS3RoleLocalIdentityProjection(
        schema=PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
        role=profiles.ARVAN_S3_FI_PUBLISHER_ROLE,
        identity_sha256=fi_publisher_identity_sha256,
        action_profile=profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
        source_site="webapp_fi",
        destination_site="webapp_ir",
        object_storage_namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
        allowed_operations=profiles.ARVAN_S3_FI_PUBLISHER_EXPECTED_ACTIONS,
    )
    ir_receiver = ArvanS3RoleLocalIdentityProjection(
        schema=PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
        role=profiles.ARVAN_S3_IR_RECEIVER_ROLE,
        identity_sha256=ir_receiver_identity_sha256,
        action_profile=profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
        source_site="webapp_fi",
        destination_site="webapp_ir",
        object_storage_namespace=PHYSICAL_WAL_NORMAL_OBJECT_STORAGE_NAMESPACE,
        allowed_operations=profiles.ARVAN_S3_IR_RECEIVER_EXPECTED_ACTIONS,
    )
    ir_publisher = ArvanS3RoleLocalIdentityProjection(
        schema=PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
        role=profiles.ARVAN_S3_IR_PUBLISHER_ROLE,
        identity_sha256=ir_publisher_identity_sha256,
        action_profile=profiles.ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
        source_site="webapp_ir",
        destination_site="webapp_fi",
        object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        allowed_operations=profiles.ARVAN_S3_IR_PUBLISHER_EXPECTED_ACTIONS,
    )
    fi_receiver = ArvanS3RoleLocalIdentityProjection(
        schema=PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
        role=profiles.ARVAN_S3_FI_RECEIVER_ROLE,
        identity_sha256=fi_receiver_identity_sha256,
        action_profile=profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
        source_site="webapp_ir",
        destination_site="webapp_fi",
        object_storage_namespace=PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        allowed_operations=profiles.ARVAN_S3_FI_RECEIVER_EXPECTED_ACTIONS,
    )
    route_binding = binder.derive_physical_ir_to_fi_object_storage_failback_binding(
        campaign_id=campaign_id,
        release_sha=release_sha,
        fi_publisher_identity_sha256=fi_publisher.identity_sha256,
        ir_receiver_identity_sha256=ir_receiver.identity_sha256,
        ir_publisher_identity_sha256=ir_publisher.identity_sha256,
        fi_receiver_identity_sha256=fi_receiver.identity_sha256,
        normal_route_policy=normal_route_policy,
        reverse_route_policy=reverse_route_policy,
    )
    verified = binder.bind_physical_arvan_s3_four_role_preflight(
        binding=route_binding,
        normal_route_policy=normal_route_policy,
        reverse_route_policy=reverse_route_policy,
        fi_publisher_projection=fi_publisher,
        ir_receiver_projection=ir_receiver,
        ir_publisher_projection=ir_publisher,
        fi_receiver_projection=fi_receiver,
    )
    return FourRoleFixture(
        binding=route_binding,
        verified_binding=verified,
        normal_route_policy=normal_route_policy,
        reverse_route_policy=reverse_route_policy,
    )
