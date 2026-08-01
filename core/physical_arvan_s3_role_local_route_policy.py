"""Neutral non-secret route policy for one local Arvan S3 role artifact.

Unlike the retired paired loader configurations, this type contains exactly
one directed Object-Storage route and no second-role profile, credential path,
identity, client, or join capability.  Each role artifact fixes its expected
direction/namespace when it validates this policy.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.physical_arvan_s3_client_factory import (
    ARVAN_S3_CLIENT_FACTORY_SCHEMA,
    RootOwnedArvanS3ClientFactoryConfig,
    validate_root_owned_arvan_s3_client_factory_config,
)


__all__ = (
    "PHYSICAL_ARVAN_S3_ROLE_LOCAL_ROUTE_POLICY_DEFAULT_ENABLED",
    "PHYSICAL_ARVAN_S3_ROLE_LOCAL_ROUTE_POLICY_SCHEMA",
    "ArvanS3RoleLocalRoutePolicy",
    "ArvanS3RoleLocalRoutePolicyError",
    "validate_physical_arvan_s3_role_local_route_policy",
)


PHYSICAL_ARVAN_S3_ROLE_LOCAL_ROUTE_POLICY_SCHEMA = (
    "gold-trade-physical-arvan-s3-role-local-route-policy-v1"
)
PHYSICAL_ARVAN_S3_ROLE_LOCAL_ROUTE_POLICY_DEFAULT_ENABLED = False


class ArvanS3RoleLocalRoutePolicyError(ValueError):
    """Fixed-code route-policy rejection without endpoint disclosure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ArvanS3RoleLocalRoutePolicy:
    """One non-secret directed route; no role pairing is represented here."""

    schema: str = PHYSICAL_ARVAN_S3_ROLE_LOCAL_ROUTE_POLICY_SCHEMA
    endpoint: str = ""
    region: str = ""
    bucket: str = ""
    enabled: bool = PHYSICAL_ARVAN_S3_ROLE_LOCAL_ROUTE_POLICY_DEFAULT_ENABLED
    source_site: str = ""
    destination_site: str = ""
    object_storage_namespace: str = ""
    direct_site_control: str = "forbidden"
    destination_object_ingest: str = "pull-only"


def _fail(code: str) -> None:
    raise ArvanS3RoleLocalRoutePolicyError(code)


def validate_physical_arvan_s3_role_local_route_policy(
    policy: ArvanS3RoleLocalRoutePolicy,
    *,
    expected_source_site: str,
    expected_destination_site: str,
    expected_object_storage_namespace: str,
    require_enabled: bool,
) -> ArvanS3RoleLocalRoutePolicy:
    """Normalize one fixed route without reading a credential or network."""

    if (
        type(policy) is not ArvanS3RoleLocalRoutePolicy
        or policy.schema != PHYSICAL_ARVAN_S3_ROLE_LOCAL_ROUTE_POLICY_SCHEMA
        or type(policy.enabled) is not bool
        or policy.source_site != expected_source_site
        or policy.destination_site != expected_destination_site
        or policy.object_storage_namespace != expected_object_storage_namespace
        or policy.direct_site_control != "forbidden"
        or policy.destination_object_ingest != "pull-only"
        or type(expected_source_site) is not str
        or type(expected_destination_site) is not str
        or type(expected_object_storage_namespace) is not str
        or not expected_source_site
        or not expected_destination_site
        or not expected_object_storage_namespace
    ):
        _fail("ARVAN_S3_ROLE_LOCAL_ROUTE_POLICY_INVALID")
    if require_enabled and policy.enabled is not True:
        _fail("ARVAN_S3_ROLE_LOCAL_ROUTE_POLICY_DISABLED")
    try:
        normalized = validate_root_owned_arvan_s3_client_factory_config(
            RootOwnedArvanS3ClientFactoryConfig(
                schema=ARVAN_S3_CLIENT_FACTORY_SCHEMA,
                endpoint=policy.endpoint,
                region=policy.region,
                bucket=policy.bucket,
                enabled=policy.enabled,
                direct_site_control="forbidden",
                destination_object_ingest="pull-only",
            )
        )
    except Exception:
        _fail("ARVAN_S3_ROLE_LOCAL_ROUTE_POLICY_INVALID")
    return ArvanS3RoleLocalRoutePolicy(
        schema=PHYSICAL_ARVAN_S3_ROLE_LOCAL_ROUTE_POLICY_SCHEMA,
        endpoint=normalized.endpoint,
        region=normalized.region,
        bucket=normalized.bucket,
        enabled=policy.enabled,
        source_site=expected_source_site,
        destination_site=expected_destination_site,
        object_storage_namespace=expected_object_storage_namespace,
        direct_site_control="forbidden",
        destination_object_ingest="pull-only",
    )
