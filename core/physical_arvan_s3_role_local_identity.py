"""Shared redacted projection grammar for one-role Arvan S3 artifacts.

This module contains no credential loader, factory, SDK, network, or route
selection.  It is deliberately the small common type accepted by the
four-role preflight binder.  A legacy paired factory projection has a
different type and is never accepted there.
"""

from __future__ import annotations

from dataclasses import dataclass


__all__ = (
    "PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA",
    "ArvanS3RoleLocalIdentityProjection",
)


PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA = (
    "gold-trade-physical-arvan-s3-role-local-identity-projection-v1"
)


@dataclass(frozen=True)
class ArvanS3RoleLocalIdentityProjection:
    """Redacted fact emitted by exactly one role-local root artifact.

    It contains no file path, credential, endpoint, bucket, SDK object, or
    client.  The binder validates the role/profile/route/action tuple before
    it treats this value as compatible with the other three local facts.
    This value is not provider IAM evidence or an execution capability.
    """

    schema: str
    role: str
    identity_sha256: str
    action_profile: str
    source_site: str
    destination_site: str
    object_storage_namespace: str
    allowed_operations: tuple[str, ...]
