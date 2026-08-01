"""Fail-closed local attestation gate for the strict durable-replay runtime.

The physical PostgreSQL scaffold deliberately refuses to render the
``strict_zero_loss`` profile: a manifest label and an adapter hash cannot
prove that the Object-Storage pull-plane runtime has been installed.  This
module does not change that refusal.  It supplies the smaller prerequisite a
future reviewed renderer/coordinator will need first: four exact,
root-controlled installation attestations bound to one already validated
strict manifest.

No network, subprocess, Docker, SSH, PostgreSQL, Object Storage, credential,
or deployment action is implemented here.  A future root-only local inspector
may be injected to read the fixed attestation files.  A successful result is
only an opaque local installation observation; it is explicitly neither a
strict-render permission, a launch permission, a write permit, nor a
Full-Matrix authorization.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    canonical_json_bytes,
)
import core.physical_postgres_deployment_scaffold as _scaffold
from core.physical_strict_remote_ack_writer_response import (
    PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA,
)
from core.physical_wa_fi_postgres_archive_command import (
    PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_RUNTIME_SCHEMA,
)
from core.physical_wal_remote_ack_object_storage_transport import (
    PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_SCHEMA,
)
from core.physical_wal_remote_ack_witness_locator_ledger import (
    PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_SCHEMA,
)


__all__ = (
    "DEFAULT_PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_MAX_EVIDENCE_AGE_SECONDS",
    "FIXED_PHYSICAL_POSTGRES_STRICT_RUNTIME_ATTESTATION_ROOT",
    "MAX_PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_MAX_EVIDENCE_AGE_SECONDS",
    "PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_ATTESTATION_SCHEMA",
    "PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_DEFAULT_ENABLED",
    "PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_GATE_SCHEMA",
    "PhysicalPostgresStrictRuntimeInstallationAttestation",
    "PhysicalPostgresStrictRuntimeInstallationConfig",
    "PhysicalPostgresStrictRuntimeInstallationError",
    "PhysicalPostgresStrictRuntimeInstallationInspector",
    "PhysicalPostgresStrictRuntimeInstallationRequest",
    "StrictDurableReplayComponentExpectation",
    "STRICT_DURABLE_REPLAY_COMPONENTS",
    "STRICT_DURABLE_REPLAY_COMPONENT_CONTRACT_SCHEMAS",
    "VerifiedPhysicalPostgresStrictRuntimeInstallations",
    "build_physical_postgres_strict_runtime_installation_request",
    "canonical_physical_postgres_strict_runtime_installation_attestation_bytes",
    "require_physical_postgres_strict_runtime_installation_request",
    "require_verified_physical_postgres_strict_runtime_installations",
    "verify_physical_postgres_strict_runtime_installations",
)


PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_GATE_SCHEMA = (
    "gold-trade-physical-postgres-strict-runtime-installation-gate-v1"
)
PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_ATTESTATION_SCHEMA = (
    "gold-trade-physical-postgres-strict-runtime-installation-attestation-v1"
)
PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_DEFAULT_ENABLED = False

DEFAULT_PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_MAX_EVIDENCE_AGE_SECONDS = 300
MAX_PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_MAX_EVIDENCE_AGE_SECONDS = 900
MAX_PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_FUTURE_SKEW_SECONDS = 5
MAX_PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_ATTESTATION_BYTES = 64 * 1024

FIXED_PHYSICAL_POSTGRES_STRICT_RUNTIME_ATTESTATION_ROOT = Path(
    "/etc/trading-bot/physical-postgres/strict-runtime"
)

STRICT_DURABLE_REPLAY_COMPONENTS = (
    "wa_fi_local_wal_archive_capture",
    "encrypted_private_versioned_object_storage_publish_receipt",
    "witness_locator_ledger",
    "writer_response_commit_boundary",
)
STRICT_DURABLE_REPLAY_COMPONENT_CONTRACT_SCHEMAS: Mapping[str, str] = {
    "wa_fi_local_wal_archive_capture": (
        PHYSICAL_WA_FI_POSTGRES_ARCHIVE_COMMAND_RUNTIME_SCHEMA
    ),
    "encrypted_private_versioned_object_storage_publish_receipt": (
        PHYSICAL_WAL_REMOTE_ACK_OBJECT_STORAGE_TRANSPORT_SCHEMA
    ),
    "witness_locator_ledger": PHYSICAL_WAL_REMOTE_ACK_WITNESS_LOCATOR_LEDGER_SCHEMA,
    "writer_response_commit_boundary": PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA,
}

_ATTESTATION_STATUS = "installed-default-off-not-launch-authorized"
_ATTESTATION_VERSION = 1
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$", re.ASCII)
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?\+00:00$",
    re.ASCII,
)
_ATTESTATION_FIELDS = frozenset(
    {
        "schema",
        "version",
        "status",
        "component",
        "component_id",
        "contract_schema",
        "contract_sha256",
        "implementation_sha256",
        "configuration_sha256",
        "installation_binding_sha256",
        "manifest_lock_sha256",
        "campaign_id",
        "release_sha",
        "route_binding_sha256",
        "writer_term_sha256",
        "strict_remote_durable_replay_identity_sha256",
        "writer_admission_integration_sha256",
        "attested_at",
        "expires_at",
        "direct_fi_to_ir_ssh",
        "direct_fi_to_ir_scp",
        "direct_fi_to_ir_postgres_control",
        "not_a_launch_authorization",
    }
)

_REQUEST_CAPABILITY = object()
_VERIFIED_CAPABILITY = object()


class PhysicalPostgresStrictRuntimeInstallationError(ValueError):
    """A fixed-code strict-runtime installation-gate failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StrictDurableReplayComponentExpectation:
    """Non-secret immutable identity expected from one installed component."""

    component_id: str
    contract_sha256: str
    implementation_sha256: str
    configuration_sha256: str
    installation_attestation_sha256: str


@dataclass(frozen=True)
class PhysicalPostgresStrictRuntimeInstallationRequest:
    """Opaque strict-manifest binding and expected local attestation hashes."""

    manifest_lock_sha256: str
    campaign_id: str
    release_sha: str
    route_binding_sha256: str
    writer_term_sha256: str
    strict_remote_durable_replay_identity_sha256: str
    writer_admission_integration_sha256: str
    installation_binding_sha256: str
    request_sha256: str
    components: tuple[tuple[str, StrictDurableReplayComponentExpectation], ...]
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def component(self, name: str) -> StrictDurableReplayComponentExpectation:
        for component, expectation in self.components:
            if component == name:
                return expectation
        raise KeyError(name)


@dataclass(frozen=True)
class PhysicalPostgresStrictRuntimeInstallationAttestation:
    """Typed local-file facts returned by an injected read-only inspector."""

    path: Path
    payload: bytes
    payload_sha256: str
    owner_uid: int
    mode: int
    regular_file: bool
    single_link: bool
    ancestors_root_controlled: bool


class PhysicalPostgresStrictRuntimeInstallationInspector(Protocol):
    """A future local reader; it must not install, launch, or contact a peer."""

    def inspect(
        self,
        *,
        component: str,
        attestation_path: Path,
    ) -> PhysicalPostgresStrictRuntimeInstallationAttestation:
        """Return one bounded local attestation-file observation."""


@dataclass(frozen=True)
class PhysicalPostgresStrictRuntimeInstallationConfig:
    """Default-off root-only inspection input with no secret material."""

    request: PhysicalPostgresStrictRuntimeInstallationRequest | None = None
    enabled: bool = PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_MAX_EVIDENCE_AGE_SECONDS
    )


@dataclass(frozen=True)
class VerifiedPhysicalPostgresStrictRuntimeInstallations:
    """Opaque local observation; deliberately not render or launch authority."""

    installation_binding_sha256: str
    request_sha256: str
    attestation_sha256es: tuple[tuple[str, str], ...]
    verified_at: datetime
    expires_at: datetime
    strict_rendering_still_refused_by_scaffold: bool = True
    not_a_launch_authorization: bool = True
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


def _fail(code: str) -> None:
    raise PhysicalPostgresStrictRuntimeInstallationError(code)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _safe_id(value: object, *, code: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    normalized = parsed.astimezone(timezone.utc)
    if normalized.isoformat() != value:
        _fail(code)
    return normalized


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_NONCANONICAL")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_NONCANONICAL")


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError):
        _fail(code)


def _normalise_expectation(value: object) -> StrictDurableReplayComponentExpectation:
    if type(value) is not StrictDurableReplayComponentExpectation:
        _fail("STRICT_RUNTIME_INSTALLATION_COMPONENT_EXPECTATION_INVALID")
    return StrictDurableReplayComponentExpectation(
        component_id=_safe_id(
            value.component_id,
            code="STRICT_RUNTIME_INSTALLATION_COMPONENT_EXPECTATION_INVALID",
        ),
        contract_sha256=_sha256(
            value.contract_sha256,
            code="STRICT_RUNTIME_INSTALLATION_COMPONENT_EXPECTATION_INVALID",
        ),
        implementation_sha256=_sha256(
            value.implementation_sha256,
            code="STRICT_RUNTIME_INSTALLATION_COMPONENT_EXPECTATION_INVALID",
        ),
        configuration_sha256=_sha256(
            value.configuration_sha256,
            code="STRICT_RUNTIME_INSTALLATION_COMPONENT_EXPECTATION_INVALID",
        ),
        installation_attestation_sha256=_sha256(
            value.installation_attestation_sha256,
            code="STRICT_RUNTIME_INSTALLATION_COMPONENT_EXPECTATION_INVALID",
        ),
    )


def _component_expectations(
    value: object,
) -> tuple[tuple[str, StrictDurableReplayComponentExpectation], ...]:
    if not isinstance(value, Mapping) or set(value) != set(STRICT_DURABLE_REPLAY_COMPONENTS):
        _fail("STRICT_RUNTIME_INSTALLATION_COMPONENT_SET_INVALID")
    return tuple(
        (component, _normalise_expectation(value[component]))
        for component in STRICT_DURABLE_REPLAY_COMPONENTS
    )


def _components_binding_mapping(
    components: tuple[tuple[str, StrictDurableReplayComponentExpectation], ...],
) -> dict[str, dict[str, str]]:
    return {
        component: {
            "component_id": expectation.component_id,
            "contract_schema": STRICT_DURABLE_REPLAY_COMPONENT_CONTRACT_SCHEMAS[
                component
            ],
            "contract_sha256": expectation.contract_sha256,
            "implementation_sha256": expectation.implementation_sha256,
            "configuration_sha256": expectation.configuration_sha256,
        }
        for component, expectation in components
    }


def _installation_binding_mapping(
    *,
    manifest_lock_sha256: str,
    campaign_id: str,
    release_sha: str,
    route_binding_sha256: str,
    writer_term_sha256: str,
    strict_remote_durable_replay_identity_sha256: str,
    writer_admission_integration_sha256: str,
    components: tuple[tuple[str, StrictDurableReplayComponentExpectation], ...],
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_GATE_SCHEMA,
        "mode": "default-off",
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "manifest_lock_sha256": manifest_lock_sha256,
        "route_binding_sha256": route_binding_sha256,
        "writer_term_sha256": writer_term_sha256,
        "strict_remote_durable_replay_identity_sha256": (
            strict_remote_durable_replay_identity_sha256
        ),
        "writer_admission_integration_sha256": writer_admission_integration_sha256,
        "components": _components_binding_mapping(components),
        "direct_fi_to_ir_ssh": False,
        "direct_fi_to_ir_scp": False,
        "direct_fi_to_ir_postgres_control": False,
        "not_a_launch_authorization": True,
    }


def _request_mapping(request: PhysicalPostgresStrictRuntimeInstallationRequest) -> dict[str, Any]:
    binding = _installation_binding_mapping(
        manifest_lock_sha256=request.manifest_lock_sha256,
        campaign_id=request.campaign_id,
        release_sha=request.release_sha,
        route_binding_sha256=request.route_binding_sha256,
        writer_term_sha256=request.writer_term_sha256,
        strict_remote_durable_replay_identity_sha256=(
            request.strict_remote_durable_replay_identity_sha256
        ),
        writer_admission_integration_sha256=request.writer_admission_integration_sha256,
        components=request.components,
    )
    return {
        **binding,
        "installation_binding_sha256": request.installation_binding_sha256,
        "expected_installation_attestation_sha256es": {
            component: expectation.installation_attestation_sha256
            for component, expectation in request.components
        },
    }


def _validated_strict_manifest(
    value: object,
) -> _scaffold.PhysicalPostgresDeploymentManifest:
    if (
        type(value) is not _scaffold.PhysicalPostgresDeploymentManifest
        or value._capability is not _scaffold._VALIDATED_MANIFEST_CAPABILITY
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_MANIFEST_INVALID")
    if value.deployment_profile != _scaffold.PROFILE_STRICT_ZERO_LOSS:
        _fail("STRICT_RUNTIME_INSTALLATION_MANIFEST_NOT_STRICT")
    writer_ack = value.adapter("writer_ack")
    if (
        writer_ack.acknowledgement_mode
        != _scaffold.ACK_MODE_STRICT_REMOTE_DURABLE_REPLAY
        or writer_ack.strict_remote_durable_replay_identity_sha256 is None
        or writer_ack.writer_admission_integration_sha256 is None
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_MANIFEST_NOT_STRICT")
    return value


def build_physical_postgres_strict_runtime_installation_request(
    manifest: _scaffold.PhysicalPostgresDeploymentManifest,
    *,
    component_expectations: Mapping[str, StrictDurableReplayComponentExpectation],
) -> PhysicalPostgresStrictRuntimeInstallationRequest:
    """Bind four exact expected attestations to one validated strict manifest.

    This function does not inspect a file or grant a render/launch capability.
    ``installation_attestation_sha256`` is intentionally excluded from the
    binding digest so a canonical attestation can bind that digest without a
    self-referential hash cycle.
    """

    validated = _validated_strict_manifest(manifest)
    components = _component_expectations(component_expectations)
    writer_ack = validated.adapter("writer_ack")
    manifest_lock_sha256 = hashlib.sha256(
        _canonical(
            validated.lock_document(),
            code="STRICT_RUNTIME_INSTALLATION_MANIFEST_INVALID",
        )
    ).hexdigest()
    binding = _installation_binding_mapping(
        manifest_lock_sha256=manifest_lock_sha256,
        campaign_id=validated.campaign_id,
        release_sha=validated.release_sha,
        route_binding_sha256=validated.route.route_binding_sha256,
        writer_term_sha256=validated.writer_term_sha256,
        strict_remote_durable_replay_identity_sha256=(
            writer_ack.strict_remote_durable_replay_identity_sha256
        ),
        writer_admission_integration_sha256=writer_ack.writer_admission_integration_sha256,
        components=components,
    )
    installation_binding_sha256 = hashlib.sha256(
        _canonical(binding, code="STRICT_RUNTIME_INSTALLATION_BINDING_INVALID")
    ).hexdigest()
    provisional = PhysicalPostgresStrictRuntimeInstallationRequest(
        manifest_lock_sha256=manifest_lock_sha256,
        campaign_id=validated.campaign_id,
        release_sha=validated.release_sha,
        route_binding_sha256=validated.route.route_binding_sha256,
        writer_term_sha256=validated.writer_term_sha256,
        strict_remote_durable_replay_identity_sha256=(
            writer_ack.strict_remote_durable_replay_identity_sha256
        ),
        writer_admission_integration_sha256=writer_ack.writer_admission_integration_sha256,
        installation_binding_sha256=installation_binding_sha256,
        request_sha256="",
        components=components,
    )
    request_sha256 = hashlib.sha256(
        _canonical(
            _request_mapping(provisional),
            code="STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID",
        )
    ).hexdigest()
    result = PhysicalPostgresStrictRuntimeInstallationRequest(
        manifest_lock_sha256=provisional.manifest_lock_sha256,
        campaign_id=provisional.campaign_id,
        release_sha=provisional.release_sha,
        route_binding_sha256=provisional.route_binding_sha256,
        writer_term_sha256=provisional.writer_term_sha256,
        strict_remote_durable_replay_identity_sha256=(
            provisional.strict_remote_durable_replay_identity_sha256
        ),
        writer_admission_integration_sha256=(
            provisional.writer_admission_integration_sha256
        ),
        installation_binding_sha256=provisional.installation_binding_sha256,
        request_sha256=request_sha256,
        components=provisional.components,
    )
    object.__setattr__(result, "_capability", _REQUEST_CAPABILITY)
    return result


def _normalise_request(
    value: object,
) -> PhysicalPostgresStrictRuntimeInstallationRequest:
    if (
        type(value) is not PhysicalPostgresStrictRuntimeInstallationRequest
        or value._capability is not _REQUEST_CAPABILITY
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID")
    if (
        not isinstance(value.components, tuple)
        or len(value.components) != len(STRICT_DURABLE_REPLAY_COMPONENTS)
        or tuple(component for component, _expectation in value.components)
        != STRICT_DURABLE_REPLAY_COMPONENTS
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID")
    components = _component_expectations(dict(value.components))
    fields = {
        "manifest_lock_sha256": _sha256(
            value.manifest_lock_sha256, code="STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID"
        ),
        "campaign_id": value.campaign_id,
        "release_sha": value.release_sha,
        "route_binding_sha256": _sha256(
            value.route_binding_sha256, code="STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID"
        ),
        "writer_term_sha256": _sha256(
            value.writer_term_sha256, code="STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID"
        ),
        "strict_remote_durable_replay_identity_sha256": _sha256(
            value.strict_remote_durable_replay_identity_sha256,
            code="STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID",
        ),
        "writer_admission_integration_sha256": _sha256(
            value.writer_admission_integration_sha256,
            code="STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID",
        ),
        "installation_binding_sha256": _sha256(
            value.installation_binding_sha256,
            code="STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID",
        ),
        "request_sha256": _sha256(
            value.request_sha256, code="STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID"
        ),
    }
    if (
        type(fields["campaign_id"]) is not str
        or CAMPAIGN_ID_RE.fullmatch(fields["campaign_id"]) is None
        or type(fields["release_sha"]) is not str
        or RELEASE_SHA_RE.fullmatch(fields["release_sha"]) is None
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID")
    normalized = PhysicalPostgresStrictRuntimeInstallationRequest(
        **fields,
        components=components,
    )
    expected_binding = hashlib.sha256(
        _canonical(
            _installation_binding_mapping(
                manifest_lock_sha256=normalized.manifest_lock_sha256,
                campaign_id=normalized.campaign_id,
                release_sha=normalized.release_sha,
                route_binding_sha256=normalized.route_binding_sha256,
                writer_term_sha256=normalized.writer_term_sha256,
                strict_remote_durable_replay_identity_sha256=(
                    normalized.strict_remote_durable_replay_identity_sha256
                ),
                writer_admission_integration_sha256=(
                    normalized.writer_admission_integration_sha256
                ),
                components=normalized.components,
            ),
            code="STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID",
        )
    ).hexdigest()
    if normalized.installation_binding_sha256 != expected_binding:
        _fail("STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID")
    expected_request = hashlib.sha256(
        _canonical(
            _request_mapping(normalized),
            code="STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID",
        )
    ).hexdigest()
    if normalized.request_sha256 != expected_request:
        _fail("STRICT_RUNTIME_INSTALLATION_REQUEST_INVALID")
    object.__setattr__(normalized, "_capability", _REQUEST_CAPABILITY)
    return normalized


def require_physical_postgres_strict_runtime_installation_request(
    value: object,
) -> PhysicalPostgresStrictRuntimeInstallationRequest:
    """Revalidate an opaque strict-installation request without file I/O.

    This is intentionally only an identity/binding recheck for other local
    fail-closed prerequisites.  It does not inspect an attestation, permit a
    strict render, launch a runtime, or authorize promotion/Full Matrix.
    """

    return _normalise_request(value)


def _normalise_config(
    value: object,
) -> tuple[PhysicalPostgresStrictRuntimeInstallationRequest, int]:
    if type(value) is not PhysicalPostgresStrictRuntimeInstallationConfig:
        _fail("STRICT_RUNTIME_INSTALLATION_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("STRICT_RUNTIME_INSTALLATION_DISABLED")
    maximum = value.maximum_evidence_age_seconds
    if (
        type(maximum) is not int
        or not 1
        <= maximum
        <= MAX_PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_MAX_EVIDENCE_AGE_SECONDS
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_CONFIG_INVALID")
    return _normalise_request(value.request), maximum


def _attestation_path(component: str) -> Path:
    if component not in STRICT_DURABLE_REPLAY_COMPONENTS:
        _fail("STRICT_RUNTIME_INSTALLATION_COMPONENT_SET_INVALID")
    return (
        FIXED_PHYSICAL_POSTGRES_STRICT_RUNTIME_ATTESTATION_ROOT
        / component
        / "installation-attestation.json"
    )


def canonical_physical_postgres_strict_runtime_installation_attestation_bytes(
    value: Mapping[str, Any],
) -> bytes:
    """Encode a typed attestation document only; this performs no file I/O."""

    if not isinstance(value, Mapping):
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_NONCANONICAL")
    return _canonical(
        dict(value), code="STRICT_RUNTIME_INSTALLATION_ATTESTATION_NONCANONICAL"
    ) + b"\n"


def _parse_attestation_payload(payload: object) -> dict[str, Any]:
    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= MAX_PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_ATTESTATION_BYTES
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_NONCANONICAL")
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_NONCANONICAL")
    if type(value) is not dict or payload != _canonical(
        value, code="STRICT_RUNTIME_INSTALLATION_ATTESTATION_NONCANONICAL"
    ) + b"\n":
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_NONCANONICAL")
    return dict(value)


def _inspect_attestation(
    *,
    inspector: PhysicalPostgresStrictRuntimeInstallationInspector,
    component: str,
) -> PhysicalPostgresStrictRuntimeInstallationAttestation:
    path = _attestation_path(component)
    try:
        observed = inspector.inspect(component=component, attestation_path=path)
    except Exception as exc:  # pragma: no cover - local inspector errors vary by OS
        raise PhysicalPostgresStrictRuntimeInstallationError(
            "STRICT_RUNTIME_INSTALLATION_ATTESTATION_UNAVAILABLE"
        ) from exc
    if type(observed) is not PhysicalPostgresStrictRuntimeInstallationAttestation:
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_INVALID")
    if observed.path != path or not isinstance(observed.path, Path):
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_PATH_INVALID")
    if (
        type(observed.owner_uid) is not int
        or observed.owner_uid != 0
        or observed.regular_file is not True
        or observed.single_link is not True
        or observed.ancestors_root_controlled is not True
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_OWNERSHIP_INVALID")
    if type(observed.mode) is not int or observed.mode != 0o600:
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_MODE_INVALID")
    if type(observed.payload) is not bytes:
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_INVALID")
    expected_payload_sha256 = hashlib.sha256(observed.payload).hexdigest()
    if (
        _sha256(
            observed.payload_sha256,
            code="STRICT_RUNTIME_INSTALLATION_ATTESTATION_HASH_MISMATCH",
        )
        != expected_payload_sha256
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_HASH_MISMATCH")
    return observed


def _verify_attestation_payload(
    *,
    payload: bytes,
    observed_payload_sha256: str,
    component: str,
    expectation: StrictDurableReplayComponentExpectation,
    request: PhysicalPostgresStrictRuntimeInstallationRequest,
    now: datetime,
    maximum_age_seconds: int,
) -> datetime:
    if observed_payload_sha256 != expectation.installation_attestation_sha256:
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_HASH_MISMATCH")
    item = _parse_attestation_payload(payload)
    if set(item) != _ATTESTATION_FIELDS:
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_BINDING_MISMATCH")
    if (
        item["schema"] != PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_ATTESTATION_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != _ATTESTATION_VERSION
        or item["status"] != _ATTESTATION_STATUS
        or item["component"] != component
        or item["component_id"] != expectation.component_id
        or item["contract_schema"]
        != STRICT_DURABLE_REPLAY_COMPONENT_CONTRACT_SCHEMAS[component]
        or item["not_a_launch_authorization"] is not True
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_BINDING_MISMATCH")
    for name, expected in (
        ("contract_sha256", expectation.contract_sha256),
        ("implementation_sha256", expectation.implementation_sha256),
        ("configuration_sha256", expectation.configuration_sha256),
        ("installation_binding_sha256", request.installation_binding_sha256),
        ("manifest_lock_sha256", request.manifest_lock_sha256),
        ("route_binding_sha256", request.route_binding_sha256),
        ("writer_term_sha256", request.writer_term_sha256),
        (
            "strict_remote_durable_replay_identity_sha256",
            request.strict_remote_durable_replay_identity_sha256,
        ),
        ("writer_admission_integration_sha256", request.writer_admission_integration_sha256),
    ):
        if _sha256(
            item[name], code="STRICT_RUNTIME_INSTALLATION_ATTESTATION_BINDING_MISMATCH"
        ) != expected:
            _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_BINDING_MISMATCH")
    if (
        item["campaign_id"] != request.campaign_id
        or item["release_sha"] != request.release_sha
        or item["direct_fi_to_ir_ssh"] is not False
        or item["direct_fi_to_ir_scp"] is not False
        or item["direct_fi_to_ir_postgres_control"] is not False
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_BINDING_MISMATCH")
    attested_at = _timestamp(
        item["attested_at"], code="STRICT_RUNTIME_INSTALLATION_ATTESTATION_TIME_INVALID"
    )
    expires_at = _timestamp(
        item["expires_at"], code="STRICT_RUNTIME_INSTALLATION_ATTESTATION_TIME_INVALID"
    )
    if attested_at > now + timedelta(
        seconds=MAX_PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_FUTURE_SKEW_SECONDS
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_FUTURE")
    if now - attested_at > timedelta(seconds=maximum_age_seconds):
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_STALE")
    if expires_at <= attested_at or expires_at - attested_at > timedelta(
        seconds=maximum_age_seconds
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_TIME_INVALID")
    if now > expires_at:
        _fail("STRICT_RUNTIME_INSTALLATION_ATTESTATION_EXPIRED")
    return expires_at


def verify_physical_postgres_strict_runtime_installations(
    *,
    config: PhysicalPostgresStrictRuntimeInstallationConfig,
    inspector: PhysicalPostgresStrictRuntimeInstallationInspector,
    now: datetime,
) -> VerifiedPhysicalPostgresStrictRuntimeInstallations:
    """Verify four local root-owned attestations without requesting a render.

    The inspector is called only after the default-off configuration, root
    runtime, request capability, and clock have all passed.  This function
    never invokes the scaffold renderer and cannot make that renderer accept
    ``strict_zero_loss``.
    """

    request, maximum_age_seconds = _normalise_config(config)
    assessed_at = _utc(now, code="STRICT_RUNTIME_INSTALLATION_CLOCK_INVALID")
    if os.geteuid() != 0:
        _fail("STRICT_RUNTIME_INSTALLATION_ROOT_RUNTIME_REQUIRED")
    observed: list[tuple[str, str]] = []
    expiry_values: list[datetime] = []
    for component, expectation in request.components:
        attestation = _inspect_attestation(inspector=inspector, component=component)
        expires_at = _verify_attestation_payload(
            payload=attestation.payload,
            observed_payload_sha256=attestation.payload_sha256,
            component=component,
            expectation=expectation,
            request=request,
            now=assessed_at,
            maximum_age_seconds=maximum_age_seconds,
        )
        observed.append((component, attestation.payload_sha256))
        expiry_values.append(expires_at)
    result = VerifiedPhysicalPostgresStrictRuntimeInstallations(
        installation_binding_sha256=request.installation_binding_sha256,
        request_sha256=request.request_sha256,
        attestation_sha256es=tuple(observed),
        verified_at=assessed_at,
        expires_at=min(expiry_values),
    )
    object.__setattr__(result, "_capability", _VERIFIED_CAPABILITY)
    return result


def require_verified_physical_postgres_strict_runtime_installations(
    value: object,
    *,
    request: PhysicalPostgresStrictRuntimeInstallationRequest,
    now: datetime,
) -> VerifiedPhysicalPostgresStrictRuntimeInstallations:
    """Recheck an opaque local observation; it remains non-authorizing."""

    expected = _normalise_request(request)
    assessed_at = _utc(now, code="STRICT_RUNTIME_INSTALLATION_CLOCK_INVALID")
    if (
        type(value) is not VerifiedPhysicalPostgresStrictRuntimeInstallations
        or value._capability is not _VERIFIED_CAPABILITY
        or value.strict_rendering_still_refused_by_scaffold is not True
        or value.not_a_launch_authorization is not True
        or value.installation_binding_sha256 != expected.installation_binding_sha256
        or value.request_sha256 != expected.request_sha256
        or value.attestation_sha256es
        != tuple(
            (component, expectation.installation_attestation_sha256)
            for component, expectation in expected.components
        )
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_VERIFIED_RESULT_INVALID")
    verified_at = _utc(value.verified_at, code="STRICT_RUNTIME_INSTALLATION_VERIFIED_RESULT_INVALID")
    expires_at = _utc(value.expires_at, code="STRICT_RUNTIME_INSTALLATION_VERIFIED_RESULT_INVALID")
    if verified_at > assessed_at + timedelta(
        seconds=MAX_PHYSICAL_POSTGRES_STRICT_RUNTIME_INSTALLATION_FUTURE_SKEW_SECONDS
    ):
        _fail("STRICT_RUNTIME_INSTALLATION_VERIFIED_RESULT_INVALID")
    if assessed_at > expires_at:
        _fail("STRICT_RUNTIME_INSTALLATION_VERIFIED_RESULT_EXPIRED")
    return value
