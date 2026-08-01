"""WA-IR-receiver-only credential-reader artifact for the normal route.

It imports neither normal paired loader nor any publisher credential path.
The one public runtime handoff validates one neutral FI→IR route policy and
opens only the fixed WA-IR exact-reader file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core import physical_arvan_s3_role_local_credential_reader as _reader
from core import physical_arvan_s3_role_profiles as _profiles
from core.physical_arvan_s3_role_local_identity import (
    PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
    ArvanS3RoleLocalIdentityProjection,
)
from core.physical_arvan_s3_role_local_route_policy import (
    ArvanS3RoleLocalRoutePolicy,
    validate_physical_arvan_s3_role_local_route_policy,
)


__all__ = (
    "ARVAN_S3_IR_RECEIVER_ROLE_LOADER_SCHEMA",
    "FIXED_ARVAN_S3_IR_RECEIVER_ROLE_CREDENTIAL_FILE",
    "ArvanS3IrReceiverRoleLoaderError",
    "RootOwnedArvanS3IrReceiverRoleLoader",
    "RootOwnedArvanS3IrReceiverRoleLoaderConfig",
    "load_root_owned_arvan_s3_ir_receiver_role_credential_facts",
    "validate_root_owned_arvan_s3_ir_receiver_role_loader_config",
)


ARVAN_S3_IR_RECEIVER_ROLE_LOADER_SCHEMA = (
    "gold-trade-physical-arvan-s3-ir-receiver-role-loader-v1"
)
FIXED_ARVAN_S3_IR_RECEIVER_ROLE_CREDENTIAL_FILE = Path(
    "/etc/trading-bot/security/arvan-s3-ir-receiver-credentials.json"
)


class ArvanS3IrReceiverRoleLoaderError(ValueError):
    """Stable redacted refusal from the receiver-only loader."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RootOwnedArvanS3IrReceiverRoleLoaderConfig:
    """One fixed normal route policy and no publisher role material."""

    schema: str = ARVAN_S3_IR_RECEIVER_ROLE_LOADER_SCHEMA
    route_policy: ArvanS3RoleLocalRoutePolicy | None = field(default=None, repr=False, compare=False)


def _fail(code: str) -> None:
    raise ArvanS3IrReceiverRoleLoaderError(code)


def _policy(value: object, *, require_enabled: bool) -> ArvanS3RoleLocalRoutePolicy:
    if (
        type(value) is not RootOwnedArvanS3IrReceiverRoleLoaderConfig
        or value.schema != ARVAN_S3_IR_RECEIVER_ROLE_LOADER_SCHEMA
        or type(value.route_policy) is not ArvanS3RoleLocalRoutePolicy
    ):
        _fail("ARVAN_S3_IR_RECEIVER_ROLE_LOADER_CONFIG_INVALID")
    try:
        return validate_physical_arvan_s3_role_local_route_policy(
            value.route_policy,
            expected_source_site="webapp_fi",
            expected_destination_site="webapp_ir",
            expected_object_storage_namespace="physical-wal",
            require_enabled=require_enabled,
        )
    except Exception:
        _fail("ARVAN_S3_IR_RECEIVER_ROLE_LOADER_CONFIG_INVALID")


def validate_root_owned_arvan_s3_ir_receiver_role_loader_config(
    config: RootOwnedArvanS3IrReceiverRoleLoaderConfig,
) -> RootOwnedArvanS3IrReceiverRoleLoaderConfig:
    """Pure validation that does not open a credential file."""

    return RootOwnedArvanS3IrReceiverRoleLoaderConfig(
        schema=ARVAN_S3_IR_RECEIVER_ROLE_LOADER_SCHEMA,
        route_policy=_policy(config, require_enabled=False),
    )


def _load(
    policy: ArvanS3RoleLocalRoutePolicy,
) -> tuple[_reader.ArvanS3RoleLocalRouteFacts, _reader.ArvanS3RoleLocalCredentialFacts]:
    try:
        return _reader.load_root_owned_arvan_s3_role_local_credential(
            route_policy=policy,
            expected_source_site="webapp_fi",
            expected_destination_site="webapp_ir",
            expected_object_storage_namespace="physical-wal",
            expected_role=_profiles.ARVAN_S3_IR_RECEIVER_ROLE,
            expected_action_profile=_profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
            fixed_credential_file=FIXED_ARVAN_S3_IR_RECEIVER_ROLE_CREDENTIAL_FILE,
        )
    except Exception:
        _fail("ARVAN_S3_IR_RECEIVER_ROLE_LOADER_CREDENTIAL_ADMISSION_FAILED")


class RootOwnedArvanS3IrReceiverRoleLoader:
    """Root-only one-role loader with no paired or publisher API."""

    __slots__ = ("_config",)

    def __init__(self, config: RootOwnedArvanS3IrReceiverRoleLoaderConfig) -> None:
        self._config = validate_root_owned_arvan_s3_ir_receiver_role_loader_config(config)

    def load_exact_receiver_credential_facts(
        self,
    ) -> tuple[_reader.ArvanS3RoleLocalRouteFacts, _reader.ArvanS3RoleLocalCredentialFacts]:
        return _load(_policy(self._config, require_enabled=True))

    def identity_projection(self) -> ArvanS3RoleLocalIdentityProjection:
        _route, credential = self.load_exact_receiver_credential_facts()
        return ArvanS3RoleLocalIdentityProjection(
            schema=PHYSICAL_ARVAN_S3_ROLE_LOCAL_IDENTITY_PROJECTION_SCHEMA,
            role=_profiles.ARVAN_S3_IR_RECEIVER_ROLE,
            identity_sha256=credential.identity_sha256,
            action_profile=_profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            object_storage_namespace="physical-wal",
            allowed_operations=_profiles.ARVAN_S3_IR_RECEIVER_EXPECTED_ACTIONS,
        )


def load_root_owned_arvan_s3_ir_receiver_role_credential_facts(
    route_policy: ArvanS3RoleLocalRoutePolicy,
) -> tuple[_reader.ArvanS3RoleLocalRouteFacts, _reader.ArvanS3RoleLocalCredentialFacts]:
    """One-role credential handoff for reviewed exact-pull runtimes only."""

    return RootOwnedArvanS3IrReceiverRoleLoader(
        RootOwnedArvanS3IrReceiverRoleLoaderConfig(route_policy=route_policy)
    ).load_exact_receiver_credential_facts()
