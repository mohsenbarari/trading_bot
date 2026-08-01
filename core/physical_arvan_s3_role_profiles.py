"""Canonical, exact Object-Storage machine-role profiles.

The normal FI publisher was historically labelled
``fi-publisher-immutable-preflight-v1`` even though its bounded authority is
the same immutable create-only publisher surface used by the four-role
failback preflight.  That label is now retired.  This module deliberately
does *not* provide an alias or compatibility conversion: a credential or
configuration carrying the retired label must be reprovisioned with the exact
canonical profile before it can participate in a normal or reverse route.

These are vocabulary and local-policy constants only.  They do not inspect a
credential, construct a client, or provide IAM/provider evidence.
"""

from __future__ import annotations


__all__ = (
    "ARVAN_S3_FI_PUBLISHER_EXPECTED_ACTIONS",
    "ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE",
    "ARVAN_S3_FI_PUBLISHER_ROLE",
    "ARVAN_S3_FI_RECEIVER_EXPECTED_ACTIONS",
    "ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE",
    "ARVAN_S3_FI_RECEIVER_ROLE",
    "ARVAN_S3_FOUR_ROLE_IDENTITY_PROFILES",
    "ARVAN_S3_IR_PUBLISHER_EXPECTED_ACTIONS",
    "ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE",
    "ARVAN_S3_IR_PUBLISHER_ROLE",
    "ARVAN_S3_IR_RECEIVER_EXPECTED_ACTIONS",
    "ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE",
    "ARVAN_S3_IR_RECEIVER_ROLE",
    "ARVAN_S3_LEGACY_FI_PUBLISHER_IMMUTABLE_PREFLIGHT_PROFILE",
    "ArvanS3RoleProfileError",
    "require_canonical_arvan_s3_role_profile",
)


ARVAN_S3_FI_PUBLISHER_ROLE = "fi-publisher"
ARVAN_S3_IR_RECEIVER_ROLE = "ir-receiver"
ARVAN_S3_IR_PUBLISHER_ROLE = "ir-publisher"
ARVAN_S3_FI_RECEIVER_ROLE = "fi-receiver"

ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE = (
    "fi-publisher-immutable-create-only-v1"
)
ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE = "ir-receiver-exact-readonly-v1"
ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE = (
    "ir-publisher-immutable-create-only-v1"
)
ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE = "fi-receiver-exact-readonly-v1"

# Kept only to make an explicit migration refusal possible.  It must never be
# returned from ``require_canonical_arvan_s3_role_profile`` or accepted by a
# credential loader, client factory, four-role binder, or preflight.
ARVAN_S3_LEGACY_FI_PUBLISHER_IMMUTABLE_PREFLIGHT_PROFILE = (
    "fi-publisher-immutable-preflight-v1"
)

ARVAN_S3_FOUR_ROLE_IDENTITY_PROFILES = (
    ("fi_publisher", ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE),
    ("ir_receiver", ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE),
    ("ir_publisher", ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE),
    ("fi_receiver", ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE),
)

# The profile names describe the narrowly allowed data-plane role.  The two
# create-only publishers still need these read-only bucket/object calls to
# carry out their bounded immutable-uploader preflight and exact readback.
ARVAN_S3_FI_PUBLISHER_EXPECTED_ACTIONS = (
    "GetBucketAcl",
    "GetBucketVersioning",
    "GetObjectLockConfiguration",
    "PutObject:create-only",
    "ListObjectVersions:exact-key",
    "GetObjectRetention:exact-version",
    "GetObject:exact-version",
    "HeadObject:exact-version",
)
ARVAN_S3_IR_RECEIVER_EXPECTED_ACTIONS = (
    "GetObject:exact-version",
    "HeadObject:exact-version",
)
ARVAN_S3_IR_PUBLISHER_EXPECTED_ACTIONS = (
    "GetBucketAcl",
    "GetBucketVersioning",
    "PutObject:create-only",
    "ListObjectVersions:exact-key",
    "GetObject:exact-version",
    "HeadObject:exact-version",
)
ARVAN_S3_FI_RECEIVER_EXPECTED_ACTIONS = (
    "GetObject:exact-version",
    "HeadObject:exact-version",
)


class ArvanS3RoleProfileError(ValueError):
    """A role/profile is not one exact canonical four-role value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_PROFILE_BY_ROLE = {
    ARVAN_S3_FI_PUBLISHER_ROLE: ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
    ARVAN_S3_IR_RECEIVER_ROLE: ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
    ARVAN_S3_IR_PUBLISHER_ROLE: ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
    ARVAN_S3_FI_RECEIVER_ROLE: ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
}


def require_canonical_arvan_s3_role_profile(*, role: object, action_profile: object) -> str:
    """Return only one exact profile; the retired FI label fails closed.

    The function intentionally has no ``legacy_ok`` argument and no alias
    table.  Reprovisioning the root-owned credential/configuration is the
    migration mechanism, rather than silently treating a broader or stale
    vocabulary as equivalent.
    """

    if type(role) is not str or role not in _PROFILE_BY_ROLE:
        raise ArvanS3RoleProfileError("ARVAN_S3_ROLE_PROFILE_ROLE_INVALID")
    if action_profile == ARVAN_S3_LEGACY_FI_PUBLISHER_IMMUTABLE_PREFLIGHT_PROFILE:
        raise ArvanS3RoleProfileError("ARVAN_S3_ROLE_PROFILE_LEGACY_MIGRATION_REQUIRED")
    if type(action_profile) is not str or action_profile != _PROFILE_BY_ROLE[role]:
        raise ArvanS3RoleProfileError("ARVAN_S3_ROLE_PROFILE_EXACT_MATCH_REQUIRED")
    return _PROFILE_BY_ROLE[role]
