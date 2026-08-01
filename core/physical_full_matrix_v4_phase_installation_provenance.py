"""Signed, default-off host-installation provenance for V4 phase adapters.

The V4 root composition and materialization preflight intentionally validate
only the *shape* of their eight injected callbacks.  That is useful before a
host implementation exists, but a callback object (including an in-process
test double) is not evidence that its corresponding host adapter has been
installed.

This module supplies the next, deliberately narrow boundary.  It accepts
only eight canonical Ed25519-signed installation attestations under a
process-local, root-built issuer policy.  The policy is pinned to one exact
V4 root composition and freshly revalidated materialization preflight.  Raw
dicts, callbacks, and look-alike policy objects are not accepted in place of
the opaque policy or signed bytes.

It performs no host, provider, Object Storage, network, Docker, process-launch,
or adapter call.  A verified result means only that the pinned issuer
attested to default-off installed adapter material at a fresh time.  It is
explicitly not installation, materialization, writer, promotion, or Full
Matrix execution authority.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from threading import RLock
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_execution_driver_v4 as _driver
from core import physical_full_matrix_v4_materialization_preflight as _materialization
from core import physical_full_matrix_v4_root_composition as _composition


__all__ = (
    "DEFAULT_PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_MAX_ATTESTATION_LIFETIME_SECONDS",
    "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_REQUIRED_PHASE_NAMES",
    "PhysicalFullMatrixV4PhaseInstallationIssuerPolicy",
    "PhysicalFullMatrixV4PhaseInstallationIssuerPolicyConfig",
    "PhysicalFullMatrixV4PhaseInstallationProvenance",
    "PhysicalFullMatrixV4PhaseInstallationProvenanceConfig",
    "PhysicalFullMatrixV4PhaseInstallationProvenanceError",
    "build_physical_full_matrix_v4_phase_installation_attestation",
    "build_physical_full_matrix_v4_phase_installation_issuer_policy",
    "require_verified_physical_full_matrix_v4_phase_installation_provenance",
    "verify_physical_full_matrix_v4_phase_installation_provenance",
)


PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-phase-installation-provenance-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-phase-installation-attestation-v1"
)
PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_DEFAULT_ENABLED: Final = False
DEFAULT_PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_MAX_ATTESTATION_LIFETIME_SECONDS: Final = (
    120
)
PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_REQUIRED_PHASE_NAMES: Final = tuple(
    phase.name for phase in _driver.PHYSICAL_FULL_MATRIX_V4_PHASES
)

_MAX_ATTESTATION_LIFETIME_SECONDS: Final = 300
_MAX_FUTURE_SKEW_SECONDS: Final = 5
_MAX_ATTESTATION_BYTES: Final = 64 * 1024
_ZERO_SHA256: Final = "0" * 64
_FORBIDDEN: Final = "forbidden"
_STATUS: Final = "signed-host-installation-attested-default-off-not-authorized"
_SIGNATURE_ALGORITHM: Final = "ed25519"
_SIGNING_DOMAIN: Final = (
    b"gold-trade-physical-full-matrix-v4-phase-installation-attestation-v1\x00"
)
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_KEY_ID_RE: Final = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_TIMESTAMP_RE: Final = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)

# The listed site is the host which owns the root phase adapter.  This does
# not replace the phase's independently required Witness/readiness evidence;
# it prevents a bare local callback from being labelled as that host work.
_PHASE_ISSUER_SITE: Final[Mapping[str, str]] = MappingProxyType(
    {
        "normal-fi-writer-v2-witness-roundtrip-strict-ack-matrix": "webapp_fi",
        "fence-fi-writer-v2": "webapp_fi",
        "recover-ir-through-object-storage-v2": "webapp_ir",
        "witness-promote-ir-v2": "webapp_ir",
        "ir-writer-v2-witness-roundtrip-strict-ack-matrix": "webapp_ir",
        "rebuild-fi-through-object-storage-v2": "webapp_fi",
        "witness-restore-fi-writer-v2": "webapp_fi",
        "final-three-site-v2-convergence-oracle": "witness",
    }
)
_ISSUER_SITES: Final = frozenset(_PHASE_ISSUER_SITE.values())
_ATTESTATION_FIELDS: Final = frozenset(
    {
        "schema",
        "version",
        "status",
        "issuer_site",
        "issuer_key_id",
        "phase_name",
        "phase_sequence",
        "oracle",
        "transport_profile",
        "destructive",
        "campaign_id",
        "release_sha",
        "run_id",
        "plan_sha256",
        "policy_sha256",
        "preflight_sha256",
        "phase_binding_sha256",
        "adapter_implementation_sha256",
        "adapter_configuration_sha256",
        "installation_binding_sha256",
        "attested_at",
        "expires_at",
        "direct_fi_to_ir_control",
        "direct_ir_to_fi_control",
        "object_storage_authority",
        "materialization_authorized",
        "host_provider_installation_authorized",
        "promotion_authorized",
        "writer_authorized",
        "execution_authorized",
        "signature_algorithm",
        "signature",
    }
)

_POLICY_CAPABILITY = object()
_RESULT_CAPABILITY = object()


class PhysicalFullMatrixV4PhaseInstallationProvenanceError(ValueError):
    """A V4 installed-adapter provenance precondition failed closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4PhaseInstallationIssuerPolicyConfig:
    """Root-only, default-off issuer pins for one materialized V4 plan.

    The keys are public, but this config is still an explicit trust bootstrap:
    a deployment must obtain them from its independently reviewed root-owned
    policy material.  Building the policy does not inspect a host or invoke
    any adapter.
    """

    enabled: bool = PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_DEFAULT_ENABLED
    materialization_preflight_config: (
        _materialization.PhysicalFullMatrixV4MaterializationPreflightConfig | None
    ) = None
    issuer_public_keys: Mapping[str, bytes] | None = None
    maximum_attestation_lifetime_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_MAX_ATTESTATION_LIFETIME_SECONDS
    )

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4PhaseInstallationIssuerPolicy:
    """Opaque policy tying host issuer keys to one V4 composition only."""

    schema: str
    issuer_policy_sha256: str
    campaign_id: str
    release_sha: str
    run_id: UUID
    plan_sha256: str
    policy_sha256: str
    preflight_sha256: str
    issuer_key_ids: Mapping[str, str]
    maximum_attestation_lifetime_seconds: int
    host_provider_installation_authorized: bool = False
    materialization_authorized: bool = False
    promotion_authorized: bool = False
    writer_authorized: bool = False
    execution_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_COPY_FORBIDDEN")


@dataclass(frozen=True)
class PhysicalFullMatrixV4PhaseInstallationProvenanceConfig:
    """Verification is default-off and yields only a diagnostic observation."""

    issuer_policy: PhysicalFullMatrixV4PhaseInstallationIssuerPolicy | None = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_DEFAULT_ENABLED


@dataclass(frozen=True, eq=False)
class PhysicalFullMatrixV4PhaseInstallationProvenance:
    """Fresh signed installation observations; never execution authority."""

    schema: str
    status: str
    issuer_policy_sha256: str
    campaign_id: str
    release_sha: str
    run_id: UUID
    plan_sha256: str
    policy_sha256: str
    preflight_sha256: str
    phase_attestation_sha256es: tuple[tuple[str, str], ...]
    verified_at: datetime
    expires_at: datetime
    signed_host_installation_observed: bool = True
    host_provider_installation_authorized: bool = False
    materialization_authorized: bool = False
    promotion_authorized: bool = False
    writer_authorized: bool = False
    execution_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_RESULT_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_RESULT_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_RESULT_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _PolicyState:
    composition: _composition.PhysicalFullMatrixV4RootComposition
    preflight: _materialization.PhysicalFullMatrixV4MaterializationPreflight
    preflight_config: _materialization.PhysicalFullMatrixV4MaterializationPreflightConfig
    issuer_public_keys: Mapping[str, bytes]
    maximum_attestation_lifetime_seconds: int
    created_at: datetime


@dataclass(frozen=True)
class _ResultState:
    policy: PhysicalFullMatrixV4PhaseInstallationIssuerPolicy
    result: PhysicalFullMatrixV4PhaseInstallationProvenance


_POLICY_STATES: WeakKeyDictionary[PhysicalFullMatrixV4PhaseInstallationIssuerPolicy, _PolicyState] = (
    WeakKeyDictionary()
)
_POLICY_CLOCK_FLOORS: WeakKeyDictionary[
    PhysicalFullMatrixV4PhaseInstallationIssuerPolicy, datetime
] = WeakKeyDictionary()
_POLICY_CLOCK_LOCK = RLock()
_RESULT_STATES: WeakKeyDictionary[PhysicalFullMatrixV4PhaseInstallationProvenance, _ResultState] = (
    WeakKeyDictionary()
)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(code) from exc


def _sha(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(code) from exc


def _render_timestamp(value: datetime, *, code: str) -> str:
    rendered = _utc(value, code=code).isoformat().replace("+00:00", "Z")
    if _TIMESTAMP_RE.fullmatch(rendered) is None:
        _fail(code)
    return rendered


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    normalized = _utc(parsed, code=code)
    if _render_timestamp(normalized, code=code) != value:
        _fail(code)
    return normalized


def _key_id(public_key: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(public_key).hexdigest()


def _root_runtime() -> None:
    try:
        if os.geteuid() != 0:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ROOT_RUNTIME_REQUIRED")
    except (AttributeError, OSError) as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _phase(name: object) -> _driver.PhysicalFullMatrixV4ExecutionPhase:
    if type(name) is not str:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PHASE_INVALID")
    for candidate in _driver.PHYSICAL_FULL_MATRIX_V4_PHASES:
        if candidate.name == name:
            return candidate
    _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PHASE_INVALID")


def _issuer_keys(value: object) -> Mapping[str, bytes]:
    if not isinstance(value, Mapping):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ISSUER_KEYS_INVALID")
    try:
        supplied = dict(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ISSUER_KEYS_INVALID"
        ) from exc
    if set(supplied) != _ISSUER_SITES:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ISSUER_KEY_SET_INVALID")
    normalized: dict[str, bytes] = {}
    key_values: set[bytes] = set()
    for site in sorted(_ISSUER_SITES):
        key = supplied[site]
        if type(key) is not bytes or len(key) != 32 or key in key_values:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ISSUER_KEYS_INVALID")
        try:
            Ed25519PublicKey.from_public_bytes(key)
        except ValueError as exc:
            raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
                "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ISSUER_KEYS_INVALID"
            ) from exc
        normalized[site] = key
        key_values.add(key)
    return MappingProxyType(normalized)


def _maximum_lifetime(value: object) -> int:
    if (
        type(value) is not int
        or not 1 <= value <= _MAX_ATTESTATION_LIFETIME_SECONDS
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_MAX_LIFETIME_INVALID")
    return value


def _require_preflight(
    value: object,
    *,
    config: object,
) -> _materialization.PhysicalFullMatrixV4MaterializationPreflight:
    if type(config) is not _materialization.PhysicalFullMatrixV4MaterializationPreflightConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PREFLIGHT_CONFIG_INVALID")
    try:
        result = _materialization.require_prepared_physical_full_matrix_v4_materialization_preflight(
            value,
            config=config,
        )
    except _materialization.PhysicalFullMatrixV4MaterializationPreflightError as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PREFLIGHT_INVALID"
        ) from exc
    if type(result) is not _materialization.PhysicalFullMatrixV4MaterializationPreflight:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PREFLIGHT_INVALID")
    return result


def _policy_config(
    value: object,
) -> tuple[
    _materialization.PhysicalFullMatrixV4MaterializationPreflightConfig,
    Mapping[str, bytes],
    int,
]:
    if type(value) is not PhysicalFullMatrixV4PhaseInstallationIssuerPolicyConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_DISABLED")
    return (
        value.materialization_preflight_config,
        _issuer_keys(value.issuer_public_keys),
        _maximum_lifetime(value.maximum_attestation_lifetime_seconds),
    )


def _policy_body(
    *,
    composition: _composition.PhysicalFullMatrixV4RootComposition,
    preflight: _materialization.PhysicalFullMatrixV4MaterializationPreflight,
    issuer_public_keys: Mapping[str, bytes],
    maximum_attestation_lifetime_seconds: int,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_SCHEMA,
        "campaign_id": composition.campaign_id,
        "release_sha": composition.release_sha,
        "run_id": str(composition.run_id),
        "plan_sha256": composition.plan_sha256,
        "policy_sha256": composition.policy_sha256,
        "preflight_sha256": preflight.preflight_sha256,
        "witness_anchor_identity_sha256": hashlib.sha256(
            _canonical(
                {
                    "schema": preflight.witness_anchor_identity.schema,
                    "journal_binding_sha256": preflight.witness_anchor_identity.journal_binding_sha256,
                    "baseline_plan_binding_sha256": preflight.witness_anchor_identity.baseline_plan_binding_sha256,
                    "run_id": str(preflight.witness_anchor_identity.run_id),
                    "plan_sha256": preflight.witness_anchor_identity.plan_sha256,
                    "anchor_genesis_sequence": preflight.witness_anchor_identity.anchor_genesis_sequence,
                    "anchor_genesis_head_sha256": preflight.witness_anchor_identity.anchor_genesis_head_sha256,
                    "canonical_genesis_sha256": preflight.witness_anchor_identity.canonical_genesis_sha256,
                },
                code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_INVALID",
            )
        ).hexdigest(),
        "maximum_attestation_lifetime_seconds": maximum_attestation_lifetime_seconds,
        "issuer_key_ids": {
            site: _key_id(issuer_public_keys[site]) for site in sorted(_ISSUER_SITES)
        },
        "phase_issuer_sites": {
            phase: _PHASE_ISSUER_SITE[phase]
            for phase in PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_REQUIRED_PHASE_NAMES
        },
        "host_provider_installation_authorized": False,
        "materialization_authorized": False,
        "promotion_authorized": False,
        "writer_authorized": False,
        "execution_authorized": False,
    }


def _policy_state(
    value: object,
) -> tuple[PhysicalFullMatrixV4PhaseInstallationIssuerPolicy, _PolicyState]:
    if (
        type(value) is not PhysicalFullMatrixV4PhaseInstallationIssuerPolicy
        or value._capability is not _POLICY_CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_INVALID")
    state = _POLICY_STATES.get(value)
    if state is None:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_SCHEMA
        or value.campaign_id != state.composition.campaign_id
        or value.release_sha != state.composition.release_sha
        or value.run_id != state.composition.run_id
        or value.plan_sha256 != state.composition.plan_sha256
        or value.policy_sha256 != state.composition.policy_sha256
        or value.preflight_sha256 != state.preflight.preflight_sha256
        or value.maximum_attestation_lifetime_seconds
        != state.maximum_attestation_lifetime_seconds
        or value.host_provider_installation_authorized is not False
        or value.materialization_authorized is not False
        or value.promotion_authorized is not False
        or value.writer_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_INVALID")
    expected_key_ids = MappingProxyType(
        {site: _key_id(state.issuer_public_keys[site]) for site in sorted(_ISSUER_SITES)}
    )
    if not isinstance(value.issuer_key_ids, Mapping):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_INVALID")
    try:
        supplied_key_ids = dict(value.issuer_key_ids)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_INVALID"
        ) from exc
    if supplied_key_ids != dict(expected_key_ids):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_INVALID")
    expected_sha256 = hashlib.sha256(
        _canonical(
            _policy_body(
                composition=state.composition,
                preflight=state.preflight,
                issuer_public_keys=state.issuer_public_keys,
                maximum_attestation_lifetime_seconds=state.maximum_attestation_lifetime_seconds,
            ),
            code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_INVALID",
        )
    ).hexdigest()
    if value.issuer_policy_sha256 != expected_sha256:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_INVALID")
    return value, state


def _trusted_now(
    *,
    composition: _composition.PhysicalFullMatrixV4RootComposition,
    floor: datetime | None,
) -> datetime:
    clock = composition.execution_adapters.trusted_clock
    callback = getattr(clock, "now_utc", None)
    if not callable(callback):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_TRUSTED_CLOCK_REQUIRED")
    try:
        observed = _utc(
            callback(),
            code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_TRUSTED_CLOCK_INVALID",
        )
    except PhysicalFullMatrixV4PhaseInstallationProvenanceError:
        raise
    except Exception as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_TRUSTED_CLOCK_FAILED"
        ) from exc
    if floor is not None and observed < floor:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_TRUSTED_CLOCK_REGRESSION")
    return observed


def _fresh_policy(
    value: object,
) -> tuple[
    PhysicalFullMatrixV4PhaseInstallationIssuerPolicy,
    _PolicyState,
    datetime,
]:
    policy, state = _policy_state(value)
    try:
        composition = _composition.require_physical_full_matrix_v4_root_composition(
            state.composition
        )
    except _composition.PhysicalFullMatrixV4RootCompositionError as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_COMPOSITION_INVALID"
        ) from exc
    if composition is not state.composition:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_COMPOSITION_INVALID")
    preflight = _require_preflight(state.preflight, config=state.preflight_config)
    if (
        preflight is not state.preflight
        or preflight.campaign_id != policy.campaign_id
        or preflight.release_sha != policy.release_sha
        or preflight.run_id != policy.run_id
        or preflight.plan_sha256 != policy.plan_sha256
        or preflight.policy_sha256 != policy.policy_sha256
        or preflight.preflight_sha256 != policy.preflight_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PREFLIGHT_INVALID")
    observed = _trusted_now(composition=composition, floor=state.created_at)
    with _POLICY_CLOCK_LOCK:
        floor = _POLICY_CLOCK_FLOORS.get(policy)
        if floor is None:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_INVALID")
        if observed < floor:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_TRUSTED_CLOCK_REGRESSION")
        _POLICY_CLOCK_FLOORS[policy] = observed
    now = observed
    return policy, state, now


def build_physical_full_matrix_v4_phase_installation_issuer_policy(
    *,
    config: PhysicalFullMatrixV4PhaseInstallationIssuerPolicyConfig,
    composition: _composition.PhysicalFullMatrixV4RootComposition,
    materialization_preflight: _materialization.PhysicalFullMatrixV4MaterializationPreflight,
) -> PhysicalFullMatrixV4PhaseInstallationIssuerPolicy:
    """Pin three trusted host issuer keys to one fresh V4 preflight.

    This is the only trust-bootstrap constructor.  It requires root runtime
    and opaque V4 inputs, but neither reads a key file nor accepts an adapter
    callback.  The returned policy itself grants no host or execution action.
    """

    preflight_config, issuer_public_keys, maximum_lifetime = _policy_config(config)
    _root_runtime()
    try:
        exact_composition = _composition.require_physical_full_matrix_v4_root_composition(
            composition
        )
    except _composition.PhysicalFullMatrixV4RootCompositionError as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_COMPOSITION_INVALID"
        ) from exc
    preflight = _require_preflight(materialization_preflight, config=preflight_config)
    if (
        preflight.campaign_id != exact_composition.campaign_id
        or preflight.release_sha != exact_composition.release_sha
        or preflight.run_id != exact_composition.run_id
        or preflight.plan_sha256 != exact_composition.plan_sha256
        or preflight.policy_sha256 != exact_composition.policy_sha256
        or tuple(preflight.adapter_names)
        != PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_REQUIRED_PHASE_NAMES
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PREFLIGHT_INVALID")
    created_at = _trusted_now(composition=exact_composition, floor=None)
    policy_sha256 = hashlib.sha256(
        _canonical(
            _policy_body(
                composition=exact_composition,
                preflight=preflight,
                issuer_public_keys=issuer_public_keys,
                maximum_attestation_lifetime_seconds=maximum_lifetime,
            ),
            code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_POLICY_INVALID",
        )
    ).hexdigest()
    result = PhysicalFullMatrixV4PhaseInstallationIssuerPolicy(
        schema=PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_SCHEMA,
        issuer_policy_sha256=policy_sha256,
        campaign_id=exact_composition.campaign_id,
        release_sha=exact_composition.release_sha,
        run_id=exact_composition.run_id,
        plan_sha256=exact_composition.plan_sha256,
        policy_sha256=exact_composition.policy_sha256,
        preflight_sha256=preflight.preflight_sha256,
        issuer_key_ids=MappingProxyType(
            {site: _key_id(issuer_public_keys[site]) for site in sorted(_ISSUER_SITES)}
        ),
        maximum_attestation_lifetime_seconds=maximum_lifetime,
    )
    object.__setattr__(result, "_capability", _POLICY_CAPABILITY)
    _POLICY_STATES[result] = _PolicyState(
        composition=exact_composition,
        preflight=preflight,
        preflight_config=preflight_config,
        issuer_public_keys=issuer_public_keys,
        maximum_attestation_lifetime_seconds=maximum_lifetime,
        created_at=created_at,
    )
    with _POLICY_CLOCK_LOCK:
        _POLICY_CLOCK_FLOORS[result] = created_at
    _policy_state(result)
    return result


def _phase_binding_sha256(
    *,
    policy: PhysicalFullMatrixV4PhaseInstallationIssuerPolicy,
    phase: _driver.PhysicalFullMatrixV4ExecutionPhase,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_SCHEMA,
                "campaign_id": policy.campaign_id,
                "release_sha": policy.release_sha,
                "run_id": str(policy.run_id),
                "plan_sha256": policy.plan_sha256,
                "policy_sha256": policy.policy_sha256,
                "preflight_sha256": policy.preflight_sha256,
                "issuer_site": _PHASE_ISSUER_SITE[phase.name],
                "phase_name": phase.name,
                "phase_sequence": phase.sequence,
                "oracle": phase.oracle,
                "transport_profile": phase.transport_profile,
                "destructive": phase.destructive,
            },
            code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_INVALID",
        )
    ).hexdigest()


def _installation_binding_sha256(
    *,
    phase_binding_sha256: str,
    adapter_implementation_sha256: str,
    adapter_configuration_sha256: str,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_SCHEMA,
                "phase_binding_sha256": phase_binding_sha256,
                "adapter_implementation_sha256": adapter_implementation_sha256,
                "adapter_configuration_sha256": adapter_configuration_sha256,
                "host_provider_installation_authorized": False,
                "materialization_authorized": False,
                "promotion_authorized": False,
                "writer_authorized": False,
                "execution_authorized": False,
            },
            code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_INVALID",
        )
    ).hexdigest()


def _attestation_body(
    *,
    policy: PhysicalFullMatrixV4PhaseInstallationIssuerPolicy,
    phase: _driver.PhysicalFullMatrixV4ExecutionPhase,
    issuer_key_id: str,
    adapter_implementation_sha256: str,
    adapter_configuration_sha256: str,
    attested_at: datetime,
    expires_at: datetime,
) -> dict[str, object]:
    phase_binding_sha256 = _phase_binding_sha256(policy=policy, phase=phase)
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_SCHEMA,
        "version": 1,
        "status": _STATUS,
        "issuer_site": _PHASE_ISSUER_SITE[phase.name],
        "issuer_key_id": issuer_key_id,
        "phase_name": phase.name,
        "phase_sequence": phase.sequence,
        "oracle": phase.oracle,
        "transport_profile": phase.transport_profile,
        "destructive": phase.destructive,
        "campaign_id": policy.campaign_id,
        "release_sha": policy.release_sha,
        "run_id": str(policy.run_id),
        "plan_sha256": policy.plan_sha256,
        "policy_sha256": policy.policy_sha256,
        "preflight_sha256": policy.preflight_sha256,
        "phase_binding_sha256": phase_binding_sha256,
        "adapter_implementation_sha256": adapter_implementation_sha256,
        "adapter_configuration_sha256": adapter_configuration_sha256,
        "installation_binding_sha256": _installation_binding_sha256(
            phase_binding_sha256=phase_binding_sha256,
            adapter_implementation_sha256=adapter_implementation_sha256,
            adapter_configuration_sha256=adapter_configuration_sha256,
        ),
        "attested_at": _render_timestamp(
            attested_at,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_TIME_INVALID",
        ),
        "expires_at": _render_timestamp(
            expires_at,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_TIME_INVALID",
        ),
        "direct_fi_to_ir_control": _FORBIDDEN,
        "direct_ir_to_fi_control": _FORBIDDEN,
        "object_storage_authority": _FORBIDDEN,
        "materialization_authorized": False,
        "host_provider_installation_authorized": False,
        "promotion_authorized": False,
        "writer_authorized": False,
        "execution_authorized": False,
        "signature_algorithm": _SIGNATURE_ALGORITHM,
    }


def build_physical_full_matrix_v4_phase_installation_attestation(
    *,
    issuer_policy: PhysicalFullMatrixV4PhaseInstallationIssuerPolicy,
    phase_name: str,
    adapter_implementation_sha256: str,
    adapter_configuration_sha256: str,
    attested_at: datetime,
    expires_at: datetime,
    issuer_private_key: Ed25519PrivateKey,
) -> bytes:
    """Create canonical signed host evidence for a future trusted issuer.

    This pure helper does not claim that installation occurred.  Verification
    accepts its output only when the caller's public key exactly matches the
    opaque policy's pinned key for this phase's owning host.
    """

    policy, state = _policy_state(issuer_policy)
    phase = _phase(phase_name)
    if not isinstance(issuer_private_key, Ed25519PrivateKey):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ISSUER_SIGNER_INVALID")
    implementation = _sha(
        adapter_implementation_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_INVALID",
    )
    configuration = _sha(
        adapter_configuration_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_INVALID",
    )
    issued = _utc(
        attested_at,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_TIME_INVALID",
    )
    expires = _utc(
        expires_at,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_TIME_INVALID",
    )
    if (
        expires <= issued
        or expires - issued
        > timedelta(seconds=state.maximum_attestation_lifetime_seconds)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_TIME_INVALID")
    try:
        public = issuer_private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ISSUER_SIGNER_INVALID"
        ) from exc
    site = _PHASE_ISSUER_SITE[phase.name]
    if public != state.issuer_public_keys[site]:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ISSUER_SIGNER_MISMATCH")
    body = _attestation_body(
        policy=policy,
        phase=phase,
        issuer_key_id=_key_id(public),
        adapter_implementation_sha256=implementation,
        adapter_configuration_sha256=configuration,
        attested_at=issued,
        expires_at=expires,
    )
    try:
        signature = issuer_private_key.sign(_SIGNING_DOMAIN + _canonical(
            body,
            code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_INVALID",
        ))
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ISSUER_SIGNER_INVALID"
        ) from exc
    envelope = {**body, "signature": base64.b64encode(signature).decode("ascii")}
    return _canonical(
        envelope,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_INVALID",
    ) + b"\n"


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_NONCANONICAL")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_NONCANONICAL")


def _parse_attestation(value: object) -> dict[str, Any]:
    if type(value) is not bytes or not 1 <= len(value) <= _MAX_ATTESTATION_BYTES:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_BYTES_INVALID")
    try:
        parsed = json.loads(
            value.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_NONCANONICAL")
    if type(parsed) is not dict or value != _canonical(
        parsed,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_NONCANONICAL",
    ) + b"\n":
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_NONCANONICAL")
    if set(parsed) != _ATTESTATION_FIELDS:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_FIELDS_INVALID")
    return dict(parsed)


def _signature(value: object) -> bytes:
    if type(value) is not str or not value or len(value) > 128:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_SIGNATURE_INVALID")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_SIGNATURE_INVALID")
    if len(decoded) != 64:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_SIGNATURE_INVALID")
    return decoded


def _verify_attestation(
    *,
    raw: bytes,
    phase: _driver.PhysicalFullMatrixV4ExecutionPhase,
    policy: PhysicalFullMatrixV4PhaseInstallationIssuerPolicy,
    state: _PolicyState,
    now: datetime,
) -> tuple[str, datetime]:
    item = _parse_attestation(raw)
    site = _PHASE_ISSUER_SITE[phase.name]
    public_key = state.issuer_public_keys[site]
    signature = _signature(item.pop("signature"))
    # Authenticate the entire canonical claim before interpreting any of its
    # fields.  An unsigned mutation must never be reported as a trusted
    # binding failure merely because a mutated field happens to be invalid.
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            _SIGNING_DOMAIN + _canonical(
                item,
                code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_NONCANONICAL",
            ),
        )
    except InvalidSignature as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_SIGNATURE_INVALID"
        ) from exc
    except ValueError as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_SIGNATURE_INVALID"
        ) from exc
    if (
        item["schema"] != PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != 1
        or item["status"] != _STATUS
        or item["issuer_site"] != site
        or item["issuer_key_id"] != _key_id(public_key)
        or item["signature_algorithm"] != _SIGNATURE_ALGORITHM
        or item["phase_name"] != phase.name
        or item["phase_sequence"] != phase.sequence
        or item["oracle"] != phase.oracle
        or item["transport_profile"] != phase.transport_profile
        or item["destructive"] is not phase.destructive
        or item["campaign_id"] != policy.campaign_id
        or item["release_sha"] != policy.release_sha
        or item["run_id"] != str(policy.run_id)
        or item["plan_sha256"] != policy.plan_sha256
        or item["policy_sha256"] != policy.policy_sha256
        or item["preflight_sha256"] != policy.preflight_sha256
        or item["direct_fi_to_ir_control"] != _FORBIDDEN
        or item["direct_ir_to_fi_control"] != _FORBIDDEN
        or item["object_storage_authority"] != _FORBIDDEN
        or item["materialization_authorized"] is not False
        or item["host_provider_installation_authorized"] is not False
        or item["promotion_authorized"] is not False
        or item["writer_authorized"] is not False
        or item["execution_authorized"] is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_BINDING_MISMATCH")
    implementation = _sha(
        item["adapter_implementation_sha256"],
        code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_BINDING_MISMATCH",
    )
    configuration = _sha(
        item["adapter_configuration_sha256"],
        code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_BINDING_MISMATCH",
    )
    phase_binding = _phase_binding_sha256(policy=policy, phase=phase)
    if (
        _sha(
            item["phase_binding_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_BINDING_MISMATCH",
        )
        != phase_binding
        or _sha(
            item["installation_binding_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_BINDING_MISMATCH",
        )
        != _installation_binding_sha256(
            phase_binding_sha256=phase_binding,
            adapter_implementation_sha256=implementation,
            adapter_configuration_sha256=configuration,
        )
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_BINDING_MISMATCH")
    issued = _parse_timestamp(
        item["attested_at"],
        code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_TIME_INVALID",
    )
    expires = _parse_timestamp(
        item["expires_at"],
        code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_TIME_INVALID",
    )
    if (
        issued > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or now - issued
        > timedelta(seconds=state.maximum_attestation_lifetime_seconds)
        or expires <= issued
        or expires - issued
        > timedelta(seconds=state.maximum_attestation_lifetime_seconds)
        or now > expires
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_STALE")
    return hashlib.sha256(raw).hexdigest(), expires


def _config(
    value: object,
) -> PhysicalFullMatrixV4PhaseInstallationIssuerPolicy:
    if type(value) is not PhysicalFullMatrixV4PhaseInstallationProvenanceConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_DISABLED")
    policy, _state = _policy_state(value.issuer_policy)
    return policy


def _attestation_mapping(value: object) -> Mapping[str, bytes]:
    if not isinstance(value, Mapping):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_SET_INVALID")
    try:
        supplied = dict(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4PhaseInstallationProvenanceError(
            "PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_SET_INVALID"
        ) from exc
    if set(supplied) != set(PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_REQUIRED_PHASE_NAMES):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_SET_INVALID")
    if any(type(value) is not bytes for value in supplied.values()):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_ATTESTATION_BYTES_INVALID")
    return MappingProxyType(
        {
            name: supplied[name]
            for name in PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_REQUIRED_PHASE_NAMES
        }
    )


def verify_physical_full_matrix_v4_phase_installation_provenance(
    *,
    config: PhysicalFullMatrixV4PhaseInstallationProvenanceConfig,
    phase_attestations: Mapping[str, bytes],
) -> PhysicalFullMatrixV4PhaseInstallationProvenance:
    """Verify all eight pinned signed host claims without invoking an adapter."""

    policy = _config(config)
    policy, state, now = _fresh_policy(policy)
    supplied = _attestation_mapping(phase_attestations)
    observed: list[tuple[str, str]] = []
    expiry_values: list[datetime] = []
    for phase in _driver.PHYSICAL_FULL_MATRIX_V4_PHASES:
        digest, expires = _verify_attestation(
            raw=supplied[phase.name],
            phase=phase,
            policy=policy,
            state=state,
            now=now,
        )
        observed.append((phase.name, digest))
        expiry_values.append(expires)
    result = PhysicalFullMatrixV4PhaseInstallationProvenance(
        schema=PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_SCHEMA,
        status=_STATUS,
        issuer_policy_sha256=policy.issuer_policy_sha256,
        campaign_id=policy.campaign_id,
        release_sha=policy.release_sha,
        run_id=policy.run_id,
        plan_sha256=policy.plan_sha256,
        policy_sha256=policy.policy_sha256,
        preflight_sha256=policy.preflight_sha256,
        phase_attestation_sha256es=tuple(observed),
        verified_at=now,
        expires_at=min(expiry_values),
    )
    object.__setattr__(result, "_capability", _RESULT_CAPABILITY)
    _RESULT_STATES[result] = _ResultState(policy=policy, result=result)
    return require_verified_physical_full_matrix_v4_phase_installation_provenance(
        result,
        config=config,
    )


def require_verified_physical_full_matrix_v4_phase_installation_provenance(
    value: object,
    *,
    config: PhysicalFullMatrixV4PhaseInstallationProvenanceConfig,
) -> PhysicalFullMatrixV4PhaseInstallationProvenance:
    """Freshly recheck opaque provenance; it still never authorizes a phase."""

    policy = _config(config)
    policy, _state, now = _fresh_policy(policy)
    if (
        type(value) is not PhysicalFullMatrixV4PhaseInstallationProvenance
        or value._capability is not _RESULT_CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_RESULT_INVALID")
    state = _RESULT_STATES.get(value)
    if state is None or state.policy is not policy or state.result is not value:
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_RESULT_INVALID")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_PROVENANCE_SCHEMA
        or value.status != _STATUS
        or value.issuer_policy_sha256 != policy.issuer_policy_sha256
        or value.campaign_id != policy.campaign_id
        or value.release_sha != policy.release_sha
        or value.run_id != policy.run_id
        or value.plan_sha256 != policy.plan_sha256
        or value.policy_sha256 != policy.policy_sha256
        or value.preflight_sha256 != policy.preflight_sha256
        or value.signed_host_installation_observed is not True
        or value.host_provider_installation_authorized is not False
        or value.materialization_authorized is not False
        or value.promotion_authorized is not False
        or value.writer_authorized is not False
        or value.execution_authorized is not False
        or tuple(name for name, _digest in value.phase_attestation_sha256es)
        != PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_REQUIRED_PHASE_NAMES
        or any(
            _SHA256_RE.fullmatch(digest) is None or digest == _ZERO_SHA256
            for _name, digest in value.phase_attestation_sha256es
        )
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_RESULT_INVALID")
    verified_at = _utc(
        value.verified_at,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_RESULT_INVALID",
    )
    expires_at = _utc(
        value.expires_at,
        code="PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_RESULT_INVALID",
    )
    if (
        verified_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or now > expires_at
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_PHASE_INSTALLATION_RESULT_EXPIRED")
    return value
