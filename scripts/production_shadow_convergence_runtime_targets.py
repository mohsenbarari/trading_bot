#!/usr/bin/env python3
"""Shared contract for the inert convergence runtime-target descriptor.

The descriptor binds a redacted, controller-local target-set artifact into
fresh prepare and cutover documents.  It is deliberately *not* a runtime
attestation: no observer, gate, or activation path may treat it as proof until
the later immutable observer/gate handoff recomputes and verifies it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import UUID


LEGACY_PREPARE_MATERIAL_SET_SCHEMA = "production-shadow-prepare-material-set-v1"
PREVIOUS_PREPARE_MATERIAL_SET_SCHEMA = "production-shadow-prepare-material-set-v2"
PREPARE_MATERIAL_SET_SCHEMA = "production-shadow-prepare-material-set-v3"
LEGACY_CUTOVER_MANIFEST_SCHEMA = "production-shadow-cutover-manifest-v1"
PREVIOUS_CUTOVER_MANIFEST_SCHEMA = "production-shadow-cutover-manifest-v2"
PREVIOUS_V3_CUTOVER_MANIFEST_SCHEMA = "production-shadow-cutover-manifest-v3"
CUTOVER_MANIFEST_SCHEMA = "production-shadow-cutover-manifest-v4"

CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA = (
    "production-shadow-convergence-runtime-target-set-v2"
)
CONVERGENCE_RUNTIME_TARGETS_FILENAME = "convergence-runtime-targets.json"
CONVERGENCE_RUNTIME_TARGET_ROLES = ("bot_fi", "webapp_fi", "webapp_ir")
CONVERGENCE_RUNTIME_IMAGE_KINDS = ("app", "postgres", "redis", "nginx")
CONVERGENCE_RUNTIME_TARGET_DOMAIN = (
    "trading-bot/production-shadow/convergence-runtime-target/v1"
)
CONVERGENCE_RUNTIME_DATABASE_ENV_FIELDS = (
    "DATABASE_URL",
    "SYNC_DATABASE_URL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)
CONVERGENCE_RUNTIME_IDENTITY_ENV_FIELDS = (
    "TZ",
    "ENVIRONMENT",
    "TOPOLOGY_SCHEMA_VERSION",
    "THREE_SITE_DR_ENABLED",
    "DR_EVENT_PROTOCOL_ENABLED",
    "DR_EVENT_PROTOCOL_STRICT",
    "RELEASE_SHA",
    "SERVER_MODE",
    "LOGICAL_AUTHORITY",
    "PHYSICAL_SITE",
)
CONVERGENCE_RUNTIME_TARGET_ROLE_FIELDS = frozenset(
    {
        "observer_service_sha256",
        "async_database_target_sha256",
        "sync_database_target_sha256",
        "runtime_identity_sha256",
        "runtime_target_descriptor_sha256",
    }
)
CONVERGENCE_RUNTIME_TARGET_SET_FIELDS = frozenset(
    {
        "schema",
        "operation_id",
        "release_sha",
        "canonical_compose_sha256",
        "roles",
        "target_set_sha256",
    }
)
CONVERGENCE_RUNTIME_BINDING_FIELDS = frozenset(
    {
        "runtime_target_row",
        "database_target_identity_sha256",
        "runtime_config_projection_sha256",
    }
)
OBSERVER_RUNTIME_TARGET_BINDING_SCHEMA = (
    "production-shadow-convergence-observer-runtime-target-binding-v1"
)
OBSERVER_RUNTIME_EXECUTION_CONTRACT = (
    "compose-network-role-sync-observer-v1"
)
OBSERVER_DUMMY_COMMAND = (
    "python",
    "-c",
    "raise SystemExit('invoke with docker compose run')",
)
OBSERVER_OPERATION_NETWORK_LABELS = {
    "trading-bot.production.operation-id": (
        "${PRODUCTION_SHADOW_OPERATION_ID:?operation UUID is required}"
    )
}
OBSERVER_RUNTIME_TARGET_BINDING_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "operation_id",
        "release_sha",
        "manifest_sha256",
        "canonical_compose_sha256",
        "role",
        "execution_contract",
        "convergence_runtime_targets",
        "runtime_target_row",
        "role_material_sha256",
        "role_runtime_image_ids",
        "database_target_identity_sha256",
        "runtime_config_projection_sha256",
        "binding_sha256",
    }
)
CONVERGENCE_RUNTIME_TARGET_DESCRIPTOR_FIELDS = frozenset(
    {
        "schema",
        "filename",
        "sha256",
        "bytes",
        "target_set_sha256",
        "roles",
    }
)
MAX_CONVERGENCE_RUNTIME_TARGET_BYTES = 64 * 1024
RUNTIME_TARGET_DESCRIPTOR_CAPABILITY = (
    "convergence-runtime-target-semantic-provenance-inert-v2"
)
RUNTIME_TARGET_CAPABILITIES = (RUNTIME_TARGET_DESCRIPTOR_CAPABILITY,)

CONVERGENCE_RUNTIME_TARGET_DERIVATION_RECEIPT_SCHEMA = (
    "production-shadow-convergence-runtime-target-derivation-receipt-v1"
)
CONVERGENCE_RUNTIME_TARGET_DERIVATION_RECEIPT_FILENAME = (
    "convergence-runtime-target-derivation-receipt.json"
)
CONVERGENCE_RUNTIME_TARGET_DERIVATION_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "operation_id",
        "release_sha",
        "template_sha256",
        "authorization_basis_sha256",
        "canonical_compose_sha256",
        "convergence_runtime_targets",
    }
)

PREPARE_V3_MIGRATION_MESSAGE = (
    "legacy v1/v2 prepare material metadata cannot be reused after the "
    "runtime-target semantic provenance migration; publish fresh v3 prepare "
    "material, build a fresh v4 cutover template, and obtain a fresh approval"
)
CUTOVER_V4_MIGRATION_MESSAGE = (
    "legacy v1/v2/v3 cutover manifest cannot be reused after the remote "
    "receiver signing-policy trust-anchor migration; build a fresh v4 "
    "template and obtain a fresh approval"
)
# Retain old exported spellings for callers that report the migration error.
CUTOVER_V3_MIGRATION_MESSAGE = CUTOVER_V4_MIGRATION_MESSAGE
PREPARE_V2_MIGRATION_MESSAGE = PREPARE_V3_MIGRATION_MESSAGE
CUTOVER_V2_MIGRATION_MESSAGE = CUTOVER_V4_MIGRATION_MESSAGE

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ZERO_SHA256 = "0" * 64
_POSTGRES_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_POSTGRES_URL_SAFE_PASSWORD_RE = re.compile(r"^[A-Za-z0-9._~-]{1,256}$")
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ConvergenceRuntimeTargetDescriptorError(ValueError):
    """The inert runtime-target descriptor or capability is not exact."""


class ConvergenceRuntimeTargetBindingError(ValueError):
    """A role-local observer environment cannot be bound without secrets."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConvergenceRuntimeTargetBindingError(
                "runtime target JSON has duplicate fields"
            )
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ConvergenceRuntimeTargetBindingError(
            "runtime target value is not canonical JSON"
        ) from exc


def domain_separated_sha256(label: str, value: Mapping[str, Any]) -> str:
    """Hash a typed nonsecret descriptor under the fixed target-set domain."""

    if not isinstance(label, str) or not label or "\x00" in label:
        raise ConvergenceRuntimeTargetBindingError("runtime target digest domain is invalid")
    return hashlib.sha256(
        CONVERGENCE_RUNTIME_TARGET_DOMAIN.encode("ascii")
        + b"\x00"
        + label.encode("ascii")
        + b"\x00"
        + _canonical_json(dict(value))
    ).hexdigest()


def _required_text(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ConvergenceRuntimeTargetBindingError(f"{label} is invalid")
    return value


def _nonzero_digest(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or _SHA256_RE.fullmatch(value) is None
        or value == _ZERO_SHA256
    ):
        raise ConvergenceRuntimeTargetBindingError(f"{label} is not a nonzero SHA-256")
    return value


def _canonical_operation_id(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ConvergenceRuntimeTargetBindingError(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ConvergenceRuntimeTargetBindingError(f"{label} is invalid") from exc
    if str(parsed) != value:
        raise ConvergenceRuntimeTargetBindingError(f"{label} is invalid")
    return value


def _validated_runtime_image_ids(value: Any, *, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(CONVERGENCE_RUNTIME_IMAGE_KINDS):
        raise ConvergenceRuntimeTargetBindingError(f"{label} fields differ")
    result = {
        kind: str(value[kind])
        for kind in CONVERGENCE_RUNTIME_IMAGE_KINDS
    }
    if (
        any(_IMAGE_ID_RE.fullmatch(item) is None for item in result.values())
        or len(set(result.values())) != len(result)
    ):
        raise ConvergenceRuntimeTargetBindingError(f"{label} is invalid")
    return result


def validate_runtime_target_row(value: Any, *, role: str, label: str) -> dict[str, str]:
    """Require one target row to be a complete redacted semantic descriptor."""

    if role not in CONVERGENCE_RUNTIME_TARGET_ROLES:
        raise ConvergenceRuntimeTargetBindingError(f"{label} role is invalid")
    if not isinstance(value, Mapping) or set(value) != CONVERGENCE_RUNTIME_TARGET_ROLE_FIELDS:
        raise ConvergenceRuntimeTargetBindingError(f"{label} fields differ")
    row = {
        field: _nonzero_digest(value.get(field), label=f"{label}.{field}")
        for field in CONVERGENCE_RUNTIME_TARGET_ROLE_FIELDS
    }
    expected_descriptor = domain_separated_sha256(
        "runtime-target-descriptor",
        {
            "role": role,
            **{
                field: row[field]
                for field in row
                if field != "runtime_target_descriptor_sha256"
            },
        },
    )
    if row["runtime_target_descriptor_sha256"] != expected_descriptor:
        raise ConvergenceRuntimeTargetBindingError(f"{label} descriptor differs")
    return row


def runtime_target_set_digest(value: Mapping[str, Any]) -> str:
    """Compute the exact nonsecret integrity digest for one target-set object."""

    if not isinstance(value, Mapping):
        raise ConvergenceRuntimeTargetBindingError("runtime target set is invalid")
    return domain_separated_sha256(
        "runtime-target-set",
        {key: item for key, item in value.items() if key != "target_set_sha256"},
    )


def validate_runtime_target_set(
    value: Any,
    *,
    operation_id: str,
    release_sha: str,
    canonical_compose_sha256: str,
    label: str,
) -> dict[str, Any]:
    """Validate a complete redacted target-set against fixed campaign inputs."""

    operation = _canonical_operation_id(operation_id, label=f"{label} operation")
    if not isinstance(release_sha, str) or re.fullmatch(r"[0-9a-f]{40}", release_sha) is None:
        raise ConvergenceRuntimeTargetBindingError(f"{label} release is invalid")
    compose_digest = _nonzero_digest(
        canonical_compose_sha256,
        label=f"{label} canonical Compose",
    )
    if (
        not isinstance(value, Mapping)
        or set(value) != CONVERGENCE_RUNTIME_TARGET_SET_FIELDS
        or value.get("schema") != CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA
        or value.get("operation_id") != operation
        or value.get("release_sha") != release_sha
        or value.get("canonical_compose_sha256") != compose_digest
    ):
        raise ConvergenceRuntimeTargetBindingError(f"{label} identity differs")
    roles = value.get("roles")
    if not isinstance(roles, Mapping) or set(roles) != set(CONVERGENCE_RUNTIME_TARGET_ROLES):
        raise ConvergenceRuntimeTargetBindingError(f"{label} role coverage differs")
    normalized = {
        "schema": CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA,
        "operation_id": operation,
        "release_sha": release_sha,
        "canonical_compose_sha256": compose_digest,
        "roles": {
            role: validate_runtime_target_row(
                roles[role],
                role=role,
                label=f"{label}.{role}",
            )
            for role in CONVERGENCE_RUNTIME_TARGET_ROLES
        },
        "target_set_sha256": _nonzero_digest(
            value.get("target_set_sha256"),
            label=f"{label} target set",
        ),
    }
    if normalized["target_set_sha256"] != runtime_target_set_digest(normalized):
        raise ConvergenceRuntimeTargetBindingError(f"{label} digest differs")
    return normalized


def runtime_target_set_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    """Build the manifest-safe descriptor after full target-set validation."""

    payload = _canonical_json(value)
    descriptor = {
        "schema": CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA,
        "filename": CONVERGENCE_RUNTIME_TARGETS_FILENAME,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "target_set_sha256": value.get("target_set_sha256"),
        "roles": list(CONVERGENCE_RUNTIME_TARGET_ROLES),
    }
    return validate_runtime_target_descriptor(
        descriptor,
        label="runtime target set descriptor",
    )


def validate_runtime_target_payload_descriptor(
    payload: bytes,
    descriptor: Mapping[str, Any],
    *,
    operation_id: str,
    release_sha: str,
    canonical_compose_sha256: str,
    label: str,
) -> dict[str, Any]:
    """Reopen canonical target-set bytes and require their exact descriptor."""

    checked_descriptor = validate_runtime_target_descriptor(descriptor, label=label)
    if (
        not isinstance(payload, bytes)
        or not 1 <= len(payload) <= MAX_CONVERGENCE_RUNTIME_TARGET_BYTES
        or len(payload) != checked_descriptor["bytes"]
        or hashlib.sha256(payload).hexdigest() != checked_descriptor["sha256"]
    ):
        raise ConvergenceRuntimeTargetBindingError(f"{label} payload differs")
    try:
        document = json.loads(payload.decode("ascii"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConvergenceRuntimeTargetBindingError(f"{label} payload is not strict JSON") from exc
    if not isinstance(document, dict) or _canonical_json(document) != payload:
        raise ConvergenceRuntimeTargetBindingError(f"{label} payload is not canonical JSON")
    normalized = validate_runtime_target_set(
        document,
        operation_id=operation_id,
        release_sha=release_sha,
        canonical_compose_sha256=canonical_compose_sha256,
        label=label,
    )
    if runtime_target_set_descriptor(normalized) != checked_descriptor:
        raise ConvergenceRuntimeTargetBindingError(f"{label} descriptor differs")
    return normalized


def parse_observer_database_target(
    value: str,
    *,
    role: str,
    expected_scheme: str,
    label: str,
) -> tuple[dict[str, Any], str]:
    """Parse one canonical Compose-network PostgreSQL URL without retaining it.

    The returned target is redacted and the password is returned only so the
    caller can prove async/sync/POSTGRES_PASSWORD consistency before dropping
    it. Neither this function nor the binding builder serializes the password.
    """

    if role not in CONVERGENCE_RUNTIME_TARGET_ROLES:
        raise ConvergenceRuntimeTargetBindingError(f"{label} role is invalid")
    text = _required_text(value, label=label)
    if "?" in text or "#" in text:
        raise ConvergenceRuntimeTargetBindingError(
            f"{label} is not a canonical database URL"
        )
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise ConvergenceRuntimeTargetBindingError(
            f"{label} is not a canonical database URL"
        ) from exc
    expected_host = f"{role}_db"
    if (
        parsed.scheme != expected_scheme
        or parsed.query
        or parsed.fragment
        or parsed.netloc.count("@") != 1
        or parsed.hostname != expected_host
        or port is not None
        or parsed.username is None
        or parsed.password is None
        or not parsed.path.startswith("/")
        or parsed.path.count("/") != 1
    ):
        raise ConvergenceRuntimeTargetBindingError(
            f"{label} is not a canonical database URL"
        )
    username = parsed.username
    password = parsed.password
    database = parsed.path[1:]
    if (
        _POSTGRES_IDENTIFIER_RE.fullmatch(username) is None
        or _POSTGRES_IDENTIFIER_RE.fullmatch(database) is None
        or _POSTGRES_URL_SAFE_PASSWORD_RE.fullmatch(password) is None
    ):
        raise ConvergenceRuntimeTargetBindingError(
            f"{label} is not a canonical database URL"
        )
    if text != f"{expected_scheme}://{username}:{password}@{expected_host}/{database}":
        raise ConvergenceRuntimeTargetBindingError(f"{label} is ambiguous")
    scheme, dialect = (
        expected_scheme.split("+", 1)
        if "+" in expected_scheme
        else (expected_scheme, "default")
    )
    return (
        {
            "scheme": scheme,
            "dialect": dialect,
            "host_service": expected_host,
            "port": 5432,
            "database": database,
            "username": username,
        },
        password,
    )


def derive_runtime_identity(
    environment: Mapping[str, Any],
    *,
    role: str,
    release_sha: str,
) -> dict[str, str]:
    """Recompute the exact nonsecret observer identity from resolved values."""

    if role not in CONVERGENCE_RUNTIME_TARGET_ROLES:
        raise ConvergenceRuntimeTargetBindingError("runtime identity role is invalid")
    if not isinstance(release_sha, str) or re.fullmatch(r"[0-9a-f]{40}", release_sha) is None:
        raise ConvergenceRuntimeTargetBindingError("runtime identity release is invalid")
    values = {
        name: _required_text(environment.get(name), label=f"runtime identity {name}")
        for name in CONVERGENCE_RUNTIME_IDENTITY_ENV_FIELDS
    }
    expected = {
        "TZ": "UTC",
        "ENVIRONMENT": "production",
        "TOPOLOGY_SCHEMA_VERSION": "three-site-dr-v1",
        "THREE_SITE_DR_ENABLED": "true",
        "DR_EVENT_PROTOCOL_ENABLED": "true",
        "DR_EVENT_PROTOCOL_STRICT": "true",
        "RELEASE_SHA": release_sha,
        "SERVER_MODE": "foreign" if role == "bot_fi" else "iran",
        "LOGICAL_AUTHORITY": "foreign" if role == "bot_fi" else "webapp",
        "PHYSICAL_SITE": role,
    }
    if values != expected:
        raise ConvergenceRuntimeTargetBindingError(
            f"runtime identity for {role} differs"
        )
    return values


def observer_service_shape(*, role: str) -> dict[str, Any]:
    """Return the one semantic Compose shape allowed for a role observer.

    This deliberately excludes environment values, mounts, and image IDs.  The
    caller validates those through their own bounded contracts; this shape is
    what the redacted target-row digest commits to.
    """

    if role not in CONVERGENCE_RUNTIME_TARGET_ROLES:
        raise ConvergenceRuntimeTargetBindingError("observer service role is invalid")
    return {
        "role": role,
        "service": f"{role}_sync_observer",
        "profiles": [f"{role.replace('_', '-')}-observe"],
        "restart": "no",
        "command": list(OBSERVER_DUMMY_COMMAND),
        "depends_on": {f"{role}_db": "service_healthy"},
        "networks": [role],
    }


def validate_canonical_observer_service(
    canonical_compose: Mapping[str, Any],
    *,
    role: str,
    label: str,
) -> dict[str, Any]:
    """Prove a canonical Compose document carries the exact observer shape.

    The target-row digest must not be calculated from a hard-coded service
    description while the rendered Compose service drifts.  This validator
    checks the service and its sole internal operation network before returning
    the small nonsecret shape that is committed by ``derive_runtime_target_binding``.
    """

    expected = observer_service_shape(role=role)
    if not isinstance(canonical_compose, Mapping):
        raise ConvergenceRuntimeTargetBindingError(f"{label} Compose is invalid")
    services = canonical_compose.get("services")
    networks = canonical_compose.get("networks")
    if not isinstance(services, Mapping) or not isinstance(networks, Mapping):
        raise ConvergenceRuntimeTargetBindingError(f"{label} Compose members are invalid")
    service = services.get(expected["service"])
    network = networks.get(role)
    if not isinstance(service, Mapping) or not isinstance(network, Mapping):
        raise ConvergenceRuntimeTargetBindingError(f"{label} observer service is unavailable")
    expected_depends_on = {f"{role}_db": {"condition": "service_healthy"}}
    expected_network = {
        "labels": dict(OBSERVER_OPERATION_NETWORK_LABELS),
        "internal": True,
    }
    if (
        service.get("profiles") != expected["profiles"]
        or service.get("restart") != expected["restart"]
        or service.get("command") != expected["command"]
        or service.get("depends_on") != expected_depends_on
        or service.get("networks") != expected["networks"]
        or network != expected_network
        or "ports" in service
        or "env_file" in service
        or service.get("network_mode") == "host"
        or "container_name" in service
    ):
        raise ConvergenceRuntimeTargetBindingError(
            f"{label} observer service definition differs"
        )
    return expected


def derive_runtime_target_binding(
    environment: Mapping[str, Any],
    *,
    role: str,
    release_sha: str,
    observer_service: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive the manifest row and runtime-safe digests from local config.

    This is the only semantic bridge permitted between root-only observer
    config and the redacted target-set. It never returns an URL or password.
    """

    if not isinstance(environment, Mapping):
        raise ConvergenceRuntimeTargetBindingError("runtime environment is invalid")
    resolved = {
        name: _required_text(environment.get(name), label=f"runtime environment {name}")
        for name in CONVERGENCE_RUNTIME_DATABASE_ENV_FIELDS
    }
    async_target, async_password = parse_observer_database_target(
        resolved["DATABASE_URL"],
        role=role,
        expected_scheme="postgresql+asyncpg",
        label=f"{role} observer DATABASE_URL",
    )
    sync_target, sync_password = parse_observer_database_target(
        resolved["SYNC_DATABASE_URL"],
        role=role,
        expected_scheme="postgresql",
        label=f"{role} observer SYNC_DATABASE_URL",
    )
    async_connection = {
        key: item for key, item in async_target.items() if key != "dialect"
    }
    sync_connection = {
        key: item for key, item in sync_target.items() if key != "dialect"
    }
    expected_username = f"{role}_observer"
    if (
        async_connection != sync_connection
        or async_password != sync_password
        or async_password != resolved["POSTGRES_PASSWORD"]
        or async_target["username"] != expected_username
        or resolved["POSTGRES_USER"] != expected_username
        or async_target["username"] != resolved["POSTGRES_USER"]
        or async_target["database"] != resolved["POSTGRES_DB"]
    ):
        raise ConvergenceRuntimeTargetBindingError(
            f"runtime database targets for {role} differ"
        )
    identity = derive_runtime_identity(
        environment,
        role=role,
        release_sha=release_sha,
    )
    expected_observer_shape = observer_service_shape(role=role)
    if observer_service is not None:
        if (
            not isinstance(observer_service, Mapping)
            or dict(observer_service) != expected_observer_shape
        ):
            raise ConvergenceRuntimeTargetBindingError(
                f"observer service definition for {role} differs"
            )
    row = {
        "observer_service_sha256": domain_separated_sha256(
            "observer-service-definition", expected_observer_shape
        ),
        "async_database_target_sha256": domain_separated_sha256(
            "database-target-async", async_target
        ),
        "sync_database_target_sha256": domain_separated_sha256(
            "database-target-sync", sync_target
        ),
        "runtime_identity_sha256": domain_separated_sha256(
            "runtime-identity", identity
        ),
    }
    row["runtime_target_descriptor_sha256"] = domain_separated_sha256(
        "runtime-target-descriptor", {"role": role, **row}
    )
    digests = runtime_target_binding_digests(
        row,
        role=role,
        release_sha=release_sha,
    )
    result = {
        "runtime_target_row": row,
        **digests,
    }
    if set(result) != CONVERGENCE_RUNTIME_BINDING_FIELDS:
        raise ConvergenceRuntimeTargetBindingError("runtime target binding fields differ")
    return result


def runtime_target_binding_digests(
    row: Mapping[str, Any],
    *,
    role: str,
    release_sha: str,
) -> dict[str, str]:
    """Return the redacted local comparison digests for one validated row."""

    checked = validate_runtime_target_row(
        row,
        role=role,
        label="runtime target binding row",
    )
    if not isinstance(release_sha, str) or re.fullmatch(r"[0-9a-f]{40}", release_sha) is None:
        raise ConvergenceRuntimeTargetBindingError("runtime target binding release is invalid")
    database_target_identity_sha256 = domain_separated_sha256(
        "database-target-identity",
        {
            "role": role,
            "async_database_target_sha256": checked["async_database_target_sha256"],
            "sync_database_target_sha256": checked["sync_database_target_sha256"],
        },
    )
    runtime_config_projection_sha256 = domain_separated_sha256(
        "runtime-config-projection",
        {
            "role": role,
            "release_sha": release_sha,
            "runtime_identity_sha256": row["runtime_identity_sha256"],
            "database_target_identity_sha256": database_target_identity_sha256,
        },
    )
    return {
        "database_target_identity_sha256": database_target_identity_sha256,
        "runtime_config_projection_sha256": runtime_config_projection_sha256,
    }


def _observer_runtime_target_binding_digest(document: Mapping[str, Any]) -> str:
    return domain_separated_sha256(
        "observer-runtime-target-binding",
        {key: value for key, value in document.items() if key != "binding_sha256"},
    )


def build_observer_runtime_target_binding(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    manifest_sha256: str,
    canonical_compose_sha256: str,
    role: str,
    convergence_runtime_targets: Mapping[str, Any],
    runtime_target_row: Mapping[str, Any],
    role_material_sha256: str,
    role_runtime_image_ids: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the root-installable nonsecret target binding for one role."""

    campaign = _canonical_operation_id(campaign_id, label="observer binding campaign")
    operation = _canonical_operation_id(operation_id, label="observer binding operation")
    if campaign == operation:
        raise ConvergenceRuntimeTargetBindingError(
            "observer binding campaign and operation must differ"
        )
    if role not in CONVERGENCE_RUNTIME_TARGET_ROLES:
        raise ConvergenceRuntimeTargetBindingError("observer binding role is invalid")
    if not isinstance(release_sha, str) or re.fullmatch(r"[0-9a-f]{40}", release_sha) is None:
        raise ConvergenceRuntimeTargetBindingError("observer binding release is invalid")
    descriptor = validate_runtime_target_descriptor(
        convergence_runtime_targets,
        label="observer binding target descriptor",
    )
    compose_digest = _nonzero_digest(
        canonical_compose_sha256,
        label="observer binding canonical Compose",
    )
    row = validate_runtime_target_row(
        runtime_target_row,
        role=role,
        label="observer binding target row",
    )
    digests = runtime_target_binding_digests(row, role=role, release_sha=release_sha)
    document: dict[str, Any] = {
        "schema": OBSERVER_RUNTIME_TARGET_BINDING_SCHEMA,
        "campaign_id": campaign,
        "operation_id": operation,
        "release_sha": release_sha,
        "manifest_sha256": _nonzero_digest(
            manifest_sha256,
            label="observer binding manifest",
        ),
        "canonical_compose_sha256": compose_digest,
        "role": role,
        "execution_contract": OBSERVER_RUNTIME_EXECUTION_CONTRACT,
        "convergence_runtime_targets": descriptor,
        "runtime_target_row": row,
        "role_material_sha256": _nonzero_digest(
            role_material_sha256,
            label="observer binding role material",
        ),
        "role_runtime_image_ids": _validated_runtime_image_ids(
            role_runtime_image_ids,
            label="observer binding runtime image IDs",
        ),
        **digests,
        "binding_sha256": _ZERO_SHA256,
    }
    document["binding_sha256"] = _observer_runtime_target_binding_digest(document)
    return document


def validate_observer_runtime_target_binding(
    value: Any,
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    manifest_sha256: str,
    role: str,
    label: str,
) -> dict[str, Any]:
    """Require a local binding to exactly match request/manifest identity."""

    if not isinstance(value, Mapping) or set(value) != OBSERVER_RUNTIME_TARGET_BINDING_FIELDS:
        raise ConvergenceRuntimeTargetBindingError(f"{label} fields differ")
    document = dict(value)
    if (
        document.get("schema") != OBSERVER_RUNTIME_TARGET_BINDING_SCHEMA
        or document.get("campaign_id") != _canonical_operation_id(
            campaign_id, label=f"{label} campaign"
        )
        or document.get("operation_id") != _canonical_operation_id(
            operation_id, label=f"{label} operation"
        )
        or document.get("release_sha") != release_sha
        or document.get("manifest_sha256") != _nonzero_digest(
            manifest_sha256, label=f"{label} manifest"
        )
        or document.get("role") != role
        or document.get("execution_contract") != OBSERVER_RUNTIME_EXECUTION_CONTRACT
    ):
        raise ConvergenceRuntimeTargetBindingError(f"{label} identity differs")
    descriptor = validate_runtime_target_descriptor(
        document["convergence_runtime_targets"],
        label=f"{label} target descriptor",
    )
    row = validate_runtime_target_row(
        document["runtime_target_row"],
        role=role,
        label=f"{label} target row",
    )
    expected = build_observer_runtime_target_binding(
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        manifest_sha256=manifest_sha256,
        canonical_compose_sha256=_nonzero_digest(
            document.get("canonical_compose_sha256"),
            label=f"{label} canonical Compose",
        ),
        role=role,
        convergence_runtime_targets=descriptor,
        runtime_target_row=row,
        role_material_sha256=_nonzero_digest(
            document.get("role_material_sha256"),
            label=f"{label} role material",
        ),
        role_runtime_image_ids=_validated_runtime_image_ids(
            document.get("role_runtime_image_ids"),
            label=f"{label} runtime image IDs",
        ),
    )
    if document != expected:
        raise ConvergenceRuntimeTargetBindingError(f"{label} digest differs")
    return expected


def is_legacy_prepare_material_schema(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("schema")
        in {
            LEGACY_PREPARE_MATERIAL_SET_SCHEMA,
            PREVIOUS_PREPARE_MATERIAL_SET_SCHEMA,
        }
    )


def is_legacy_cutover_manifest_schema(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("schema")
        in {
            LEGACY_CUTOVER_MANIFEST_SCHEMA,
            PREVIOUS_CUTOVER_MANIFEST_SCHEMA,
            PREVIOUS_V3_CUTOVER_MANIFEST_SCHEMA,
        }
    )


def validate_runtime_target_capabilities(
    value: Any,
    *,
    label: str,
) -> list[str]:
    """Require the one explicit, non-activation capability declaration."""

    expected = list(RUNTIME_TARGET_CAPABILITIES)
    if not isinstance(value, list) or value != expected:
        raise ConvergenceRuntimeTargetDescriptorError(
            f"{label} must declare exactly the inert runtime-target capability"
        )
    return list(value)


def validate_runtime_target_descriptor(
    value: Any,
    *,
    label: str,
) -> dict[str, Any]:
    """Validate only the manifest-safe target-set descriptor.

    This intentionally does not read or validate the target-set payload.  The
    producer/template own that stronger file-level validation; downstream
    consumers may only bind this exact descriptor to their already validated
    manifest.
    """

    if not isinstance(value, Mapping):
        raise ConvergenceRuntimeTargetDescriptorError(f"{label} is not an object")
    descriptor = dict(value)
    if set(descriptor) != CONVERGENCE_RUNTIME_TARGET_DESCRIPTOR_FIELDS:
        raise ConvergenceRuntimeTargetDescriptorError(
            f"{label} fields differ from the exact contract"
        )
    if (
        descriptor["schema"] != CONVERGENCE_RUNTIME_TARGET_SET_SCHEMA
        or descriptor["filename"] != CONVERGENCE_RUNTIME_TARGETS_FILENAME
        or descriptor["roles"] != list(CONVERGENCE_RUNTIME_TARGET_ROLES)
    ):
        raise ConvergenceRuntimeTargetDescriptorError(
            f"{label} identity or role coverage is invalid"
        )
    if (
        isinstance(descriptor["bytes"], bool)
        or not isinstance(descriptor["bytes"], int)
        or not 1 <= descriptor["bytes"] <= MAX_CONVERGENCE_RUNTIME_TARGET_BYTES
    ):
        raise ConvergenceRuntimeTargetDescriptorError(
            f"{label} byte length is outside the exact bound"
        )
    for field in ("sha256", "target_set_sha256"):
        digest = descriptor[field]
        if (
            not isinstance(digest, str)
            or _SHA256_RE.fullmatch(digest) is None
            or digest == _ZERO_SHA256
        ):
            raise ConvergenceRuntimeTargetDescriptorError(
                f"{label}.{field} is not a nonzero SHA-256"
            )
    return descriptor


def runtime_target_derivation_receipt_path(manifest_path: Path) -> Path:
    """Return the fixed controller-local sidecar path for one manifest.

    The receipt is deliberately not represented in the manifest itself: doing
    so would make a receipt that binds the template digest circular.  The
    fixed sibling path lets the finalizer publish the same create-only receipt
    beside the final manifest and lets controller entrypoints reopen it.
    """

    if (
        not isinstance(manifest_path, Path)
        or not manifest_path.is_absolute()
        or ".." in manifest_path.parts
        or manifest_path.name in {"", ".", ".."}
    ):
        raise ConvergenceRuntimeTargetDescriptorError(
            "runtime target derivation receipt manifest path is unsafe"
        )
    return manifest_path.parent / CONVERGENCE_RUNTIME_TARGET_DERIVATION_RECEIPT_FILENAME


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or _SHA256_RE.fullmatch(value) is None
        or value == _ZERO_SHA256
    ):
        raise ConvergenceRuntimeTargetDescriptorError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def build_runtime_target_derivation_receipt(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    template_sha256: str,
    authorization_basis_sha256: str,
    canonical_compose_sha256: str,
    convergence_runtime_targets: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the nonsecret local receipt emitted only by template building."""

    if any(
        not isinstance(value, str) or not value
        for value in (campaign_id, operation_id, release_sha)
    ):
        raise ConvergenceRuntimeTargetDescriptorError(
            "runtime target derivation receipt identity is invalid"
        )
    descriptor = validate_runtime_target_descriptor(
        convergence_runtime_targets,
        label="runtime target derivation receipt descriptor",
    )
    return {
        "schema": CONVERGENCE_RUNTIME_TARGET_DERIVATION_RECEIPT_SCHEMA,
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "template_sha256": _nonzero_sha256(
            template_sha256,
            label="runtime target derivation receipt template",
        ),
        "authorization_basis_sha256": _nonzero_sha256(
            authorization_basis_sha256,
            label="runtime target derivation receipt authorization basis",
        ),
        "canonical_compose_sha256": _nonzero_sha256(
            canonical_compose_sha256,
            label="runtime target derivation receipt canonical Compose",
        ),
        "convergence_runtime_targets": descriptor,
    }


def validate_runtime_target_derivation_receipt(
    value: Any,
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    template_sha256: str,
    authorization_basis_sha256: str,
    canonical_compose_sha256: str,
    convergence_runtime_targets: Mapping[str, Any],
    label: str,
) -> dict[str, Any]:
    """Require one receipt to bind the exact pending template provenance."""

    if not isinstance(value, Mapping):
        raise ConvergenceRuntimeTargetDescriptorError(f"{label} is not an object")
    receipt = dict(value)
    if set(receipt) != CONVERGENCE_RUNTIME_TARGET_DERIVATION_RECEIPT_FIELDS:
        raise ConvergenceRuntimeTargetDescriptorError(
            f"{label} fields differ from the exact contract"
        )
    expected = build_runtime_target_derivation_receipt(
        campaign_id=campaign_id,
        operation_id=operation_id,
        release_sha=release_sha,
        template_sha256=template_sha256,
        authorization_basis_sha256=authorization_basis_sha256,
        canonical_compose_sha256=canonical_compose_sha256,
        convergence_runtime_targets=convergence_runtime_targets,
    )
    if receipt != expected:
        raise ConvergenceRuntimeTargetDescriptorError(
            f"{label} does not bind the exact pending template provenance"
        )
    return expected
