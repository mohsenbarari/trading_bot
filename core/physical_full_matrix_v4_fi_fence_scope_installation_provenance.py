"""Pure, default-off provenance for the physical V4 Phase-2 FI fence.

The existing Phase-2 retired-FI contract deliberately treats six external
digests as correlation pins.  That is necessary, but it is not enough to
prove that a physical fencer was installed with a complete scope.  In
particular, a single generic ``policy_sha256`` says nothing about application,
database, service-manager, or provider write paths.

This module is the narrow evidence boundary before that contract.  It parses
and verifies two independently signed *structured* scope policies and their
two independently signed installation attestations.  The policy grammar has
one fixed mandatory coverage set; callers cannot replace it with a generic
hash or a partial list.  Its only composable output is a projection into
``RetiredFiPredecessorFenceEvidencePins`` for the already-existing P2
verification contract.

It never fences a host, changes PostgreSQL, stops a unit, revokes a provider
credential, contacts Witness/Object Storage, opens a socket, starts a process,
or invokes an executor.  Verification is evidence-only and all authority
flags remain false.  A future root-owned physical executor and independent
observer must still do, attest, and separately prove the real operations.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Final
from weakref import WeakKeyDictionary

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.append_only_sync_delta_batch import canonical_json_bytes
from core import physical_full_matrix_v4_retired_fi_predecessor_fence as _p2


__all__ = (
    "DEFAULT_PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_MAX_ATTESTATION_LIFETIME_SECONDS",
    "PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_STATUS",
    "PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_POLICY_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V4_FI_FENCE_REQUIRED_COVERAGE",
    "PhysicalFullMatrixV4FiFenceScopeInstallationEvidence",
    "PhysicalFullMatrixV4FiFenceScopeInstallationError",
    "PhysicalFullMatrixV4FiFenceScopeInstallationVerificationConfig",
    "VerifiedPhysicalFullMatrixV4FiFenceScopeInstallationProvenance",
    "project_physical_full_matrix_v4_fi_fence_scope_installation_evidence_pins",
    "require_verified_physical_full_matrix_v4_fi_fence_scope_installation_provenance",
    "verify_physical_full_matrix_v4_fi_fence_scope_installation_provenance",
)


PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-fi-fence-scope-installation-provenance-v1"
)
PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_POLICY_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-fi-fence-scope-policy-v1"
)
PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_SCHEMA: Final = (
    "gold-trade-physical-full-matrix-v4-fi-fence-installation-attestation-v1"
)
PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_DEFAULT_ENABLED: Final = False
PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_STATUS: Final = (
    "fi-fence-scope-and-installation-verified-evidence-only"
)
DEFAULT_PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_MAX_ATTESTATION_LIFETIME_SECONDS: Final = (
    90
)

_MAX_ATTESTATION_LIFETIME_SECONDS: Final = 300
_MAX_FUTURE_SKEW_SECONDS: Final = 5
_MAX_WIRE_BYTES: Final = 64 * 1024
_SIGNATURE_ALGORITHM: Final = "ed25519"
_FORBIDDEN: Final = "forbidden"
_SCOPE_STATUS: Final = "mandatory-fi-fence-scope-policy-evidence-only"
_INSTALLATION_STATUS: Final = "fi-fence-installation-attested-evidence-only"
_SCOPE_MODE: Final = "fixed-mandatory-fi-writer-fence-coverage-v1"
_EXECUTOR_ROLE: Final = "fi-root-fence-executor"
_OBSERVER_ROLE: Final = "fi-independent-fence-observer"
_ROLE_TO_DOMAIN: Final[Mapping[str, bytes]] = MappingProxyType(
    {
        _EXECUTOR_ROLE: (
            b"gold-trade-physical-full-matrix-v4-fi-fence-scope-policy-executor-v1\x00"
        ),
        _OBSERVER_ROLE: (
            b"gold-trade-physical-full-matrix-v4-fi-fence-scope-policy-observer-v1\x00"
        ),
    }
)
_ROLE_TO_INSTALLATION_DOMAIN: Final[Mapping[str, bytes]] = MappingProxyType(
    {
        _EXECUTOR_ROLE: (
            b"gold-trade-physical-full-matrix-v4-fi-fence-installation-executor-v1\x00"
        ),
        _OBSERVER_ROLE: (
            b"gold-trade-physical-full-matrix-v4-fi-fence-installation-observer-v1\x00"
        ),
    }
)
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_KEY_ID_RE: Final = re.compile(r"^ed25519-sha256:[0-9a-f]{64}$", re.ASCII)
_TIMESTAMP_RE: Final = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$",
    re.ASCII,
)

# This is deliberately an exact structured policy, not a free-form list and
# not a hash supplied by the caller.  It names each required enforcement
# surface and the required postcondition the later physical executor/observer
# must attest.  The verifier never mistakes this *coverage requirement* for
# successful enforcement.
PHYSICAL_FULL_MATRIX_V4_FI_FENCE_REQUIRED_COVERAGE: Final[Mapping[str, Mapping[str, str]]] = (
    MappingProxyType(
        {
            "application_writer_surfaces": MappingProxyType(
                {
                    "app": "must-block-write-entrypoints",
                    "bot": "must-block-write-entrypoints",
                    "sync": "must-block-write-entrypoints",
                    "migration": "must-block-write-entrypoints",
                }
            ),
            "database_server_side": MappingProxyType(
                {
                    "write_revocation": "must-revoke-server-side-write-authority",
                    "write_session_drain": "must-drain-preexisting-write-sessions",
                }
            ),
            "activation_and_deploy_units": MappingProxyType(
                {
                    "systemd_service_units": "must-disable-stop-and-prevent-restart",
                    "systemd_socket_units": "must-disable-stop-and-prevent-activation",
                    "systemd_timer_units": "must-disable-stop-and-prevent-activation",
                    "systemd_path_units": "must-disable-stop-and-prevent-activation",
                    "systemd_restart_units": "must-disable-restart-paths",
                    "deploy_units": "must-disable-deploy-and-restart-paths",
                }
            ),
            "provider_write_paths": MappingProxyType(
                {
                    "credential_revocation": "must-revoke-writer-capable-provider-credentials",
                    "egress_revocation": "must-revoke-writer-capable-provider-egress",
                }
            ),
        }
    )
)
_REQUIRED_COVERAGE_CANONICAL: Final[dict[str, dict[str, str]]] = {
    group: dict(values)
    for group, values in PHYSICAL_FULL_MATRIX_V4_FI_FENCE_REQUIRED_COVERAGE.items()
}

_SCOPE_FIELDS: Final = frozenset(
    {
        "schema",
        "version",
        "status",
        "scope_mode",
        "signer_role",
        "signer_key_id",
        "p2_binding_sha256",
        "phase2_effect_start",
        "phase2_effect_start_anchor",
        "predecessor_term",
        "mandatory_coverage",
        "scope_policy_binding_sha256",
        "writer_authorized",
        "promotion_authorized",
        "external_effect_authorized",
        "installation_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "signature_algorithm",
        "signature",
    }
)
_INSTALLATION_FIELDS: Final = frozenset(
    {
        "schema",
        "version",
        "status",
        "signer_role",
        "signer_key_id",
        "p2_binding_sha256",
        "scope_policy_sha256",
        "scope_policy_binding_sha256",
        "installation_implementation_sha256",
        "installation_configuration_sha256",
        "installation_binding_sha256",
        "installed_at",
        "expires_at",
        "writer_authorized",
        "promotion_authorized",
        "external_effect_authorized",
        "installation_authorized",
        "execution_authorized",
        "full_matrix_authorized",
        "signature_algorithm",
        "signature",
    }
)

_CAPABILITY = object()


class PhysicalFullMatrixV4FiFenceScopeInstallationError(ValueError):
    """A P2 FI fence scope/installation provenance check failed closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4FiFenceScopeInstallationError(code)


@dataclass(frozen=True)
class PhysicalFullMatrixV4FiFenceScopeInstallationVerificationConfig:
    """Trust bootstrap for one exact P2 effect, defaulting to disabled.

    The two signing keys must differ.  They represent independent executor and
    observer evidence roles, not host access, a process handle, or an
    operational permission.
    """

    expected_effect_start: _p2.PhysicalFullMatrixV4EffectStartPin | None = None
    expected_effect_start_anchor: _p2.PhysicalFullMatrixV4EffectStartAnchorPin | None = None
    expected_predecessor_term: _p2.RetiredFiPredecessorFenceTermPin | None = None
    executor_signer_public_key: bytes = b""
    observer_signer_public_key: bytes = b""
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_DEFAULT_ENABLED
    maximum_attestation_lifetime_seconds: int = (
        DEFAULT_PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_MAX_ATTESTATION_LIFETIME_SECONDS
    )


@dataclass(frozen=True)
class PhysicalFullMatrixV4FiFenceScopeInstallationEvidence:
    """Four canonical signed evidence artifacts, never an executor request."""

    executor_scope_policy: bytes | None = field(default=None, repr=False, compare=False)
    executor_installation_attestation: bytes | None = field(
        default=None, repr=False, compare=False
    )
    observer_scope_policy: bytes | None = field(default=None, repr=False, compare=False)
    observer_installation_attestation: bytes | None = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True, eq=False, init=False)
class VerifiedPhysicalFullMatrixV4FiFenceScopeInstallationProvenance:
    """Opaque P2 provenance; it cannot authorize an operation.

    Only hashes and correlation pins are public.  The verified canonical
    evidence remains in process-local verifier state so this object cannot be
    reused as a transport bundle or a substitute physical fence receipt.
    """

    schema: str
    status: str
    provenance_sha256: str
    verified_at: datetime
    run_id: str
    plan_sha256: str
    phase2_effect_start_identity_sha256: str
    phase2_anchor_sequence: int
    phase2_anchor_head_sha256: str
    predecessor_writer_epoch: int
    predecessor_writer_lease_id: str
    executor_signer_key_id: str
    observer_signer_key_id: str
    executor_scope_policy_sha256: str
    executor_scope_policy_binding_sha256: str
    executor_installation_attestation_sha256: str
    executor_installation_binding_sha256: str
    observer_scope_policy_sha256: str
    observer_scope_policy_binding_sha256: str
    observer_installation_attestation_sha256: str
    observer_installation_binding_sha256: str
    writer_authorized: bool = False
    promotion_authorized: bool = False
    external_effect_authorized: bool = False
    installation_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    _capability: object | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        *,
        schema: str,
        status: str,
        provenance_sha256: str,
        verified_at: datetime,
        run_id: str,
        plan_sha256: str,
        phase2_effect_start_identity_sha256: str,
        phase2_anchor_sequence: int,
        phase2_anchor_head_sha256: str,
        predecessor_writer_epoch: int,
        predecessor_writer_lease_id: str,
        executor_signer_key_id: str,
        observer_signer_key_id: str,
        executor_scope_policy_sha256: str,
        executor_scope_policy_binding_sha256: str,
        executor_installation_attestation_sha256: str,
        executor_installation_binding_sha256: str,
        observer_scope_policy_sha256: str,
        observer_scope_policy_binding_sha256: str,
        observer_installation_attestation_sha256: str,
        observer_installation_binding_sha256: str,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("schema", schema),
            ("status", status),
            ("provenance_sha256", provenance_sha256),
            ("verified_at", verified_at),
            ("run_id", run_id),
            ("plan_sha256", plan_sha256),
            ("phase2_effect_start_identity_sha256", phase2_effect_start_identity_sha256),
            ("phase2_anchor_sequence", phase2_anchor_sequence),
            ("phase2_anchor_head_sha256", phase2_anchor_head_sha256),
            ("predecessor_writer_epoch", predecessor_writer_epoch),
            ("predecessor_writer_lease_id", predecessor_writer_lease_id),
            ("executor_signer_key_id", executor_signer_key_id),
            ("observer_signer_key_id", observer_signer_key_id),
            ("executor_scope_policy_sha256", executor_scope_policy_sha256),
            ("executor_scope_policy_binding_sha256", executor_scope_policy_binding_sha256),
            ("executor_installation_attestation_sha256", executor_installation_attestation_sha256),
            ("executor_installation_binding_sha256", executor_installation_binding_sha256),
            ("observer_scope_policy_sha256", observer_scope_policy_sha256),
            ("observer_scope_policy_binding_sha256", observer_scope_policy_binding_sha256),
            ("observer_installation_attestation_sha256", observer_installation_attestation_sha256),
            ("observer_installation_binding_sha256", observer_installation_binding_sha256),
            ("writer_authorized", False),
            ("promotion_authorized", False),
            ("external_effect_authorized", False),
            ("installation_authorized", False),
            ("execution_authorized", False),
            ("full_matrix_authorized", False),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _ConfigFacts:
    effect_start: _p2.PhysicalFullMatrixV4EffectStartPin
    effect_start_mapping: dict[str, Any]
    effect_start_anchor: _p2.PhysicalFullMatrixV4EffectStartAnchorPin
    effect_start_anchor_mapping: dict[str, Any]
    predecessor_term: _p2.RetiredFiPredecessorFenceTermPin
    predecessor_term_mapping: dict[str, Any]
    p2_binding_sha256: str
    executor_public_key: bytes
    observer_public_key: bytes
    maximum_lifetime_seconds: int


@dataclass(frozen=True)
class _ScopeFacts:
    sha256: str
    binding_sha256: str


@dataclass(frozen=True)
class _InstallationFacts:
    sha256: str
    binding_sha256: str


@dataclass(frozen=True)
class _ResultState:
    facts: _ConfigFacts
    public_values: tuple[object, ...]


_RESULT_STATES: WeakKeyDictionary[
    VerifiedPhysicalFullMatrixV4FiFenceScopeInstallationProvenance, _ResultState
] = WeakKeyDictionary()
_PUBLIC_FIELDS: Final = tuple(
    name
    for name in VerifiedPhysicalFullMatrixV4FiFenceScopeInstallationProvenance.__dataclass_fields__
    if name != "_capability"
)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixV4FiFenceScopeInstallationError(code) from exc


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _key(value: object, *, code: str) -> bytes:
    if type(value) is not bytes or len(value) != 32:
        _fail(code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError as exc:
        raise PhysicalFullMatrixV4FiFenceScopeInstallationError(code) from exc
    return value


def _key_id(value: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(value).hexdigest()


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _render_timestamp(value: datetime, *, code: str) -> str:
    result = _utc(value, code=code).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if _TIMESTAMP_RE.fullmatch(result) is None:
        _fail(code)
    return result


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PhysicalFullMatrixV4FiFenceScopeInstallationError(code) from exc
    return _utc(parsed, code=code)


def _p2_facts(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalFullMatrixV4FiFenceScopeInstallationVerificationConfig:
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_DISABLED")
    code = "PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_CONFIG_INVALID"
    try:
        effect_start, effect_mapping = _p2._effect_start_mapping(
            value.expected_effect_start, code=code
        )
        anchor, anchor_mapping = _p2._effect_start_anchor_mapping(
            value.expected_effect_start_anchor, code=code
        )
        term, term_mapping = _p2._term_mapping(value.expected_predecessor_term, code=code)
        if _p2._anchor_effect_start(anchor, code=code) != effect_start:
            _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_CONFIG_ANCHOR_MISMATCH")
        if not _p2._term_matches_effect_start(term, effect_start):
            _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_CONFIG_TERM_MISMATCH")
    except _p2.RetiredFiPredecessorFenceError as exc:
        raise PhysicalFullMatrixV4FiFenceScopeInstallationError(code) from exc
    executor_key = _key(value.executor_signer_public_key, code=code)
    observer_key = _key(value.observer_signer_public_key, code=code)
    if executor_key == observer_key:
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_SIGNER_SEPARATION_REQUIRED")
    if (
        type(value.maximum_attestation_lifetime_seconds) is not int
        or not 1
        <= value.maximum_attestation_lifetime_seconds
        <= _MAX_ATTESTATION_LIFETIME_SECONDS
    ):
        _fail(code)
    binding = {
        "schema": PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_SCHEMA,
        "phase2_effect_start": effect_mapping,
        "phase2_effect_start_anchor": anchor_mapping,
        "predecessor_term": term_mapping,
        "writer_authorized": False,
        "promotion_authorized": False,
        "external_effect_authorized": False,
        "installation_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
    }
    return _ConfigFacts(
        effect_start=effect_start,
        effect_start_mapping=effect_mapping,
        effect_start_anchor=anchor,
        effect_start_anchor_mapping=anchor_mapping,
        predecessor_term=term,
        predecessor_term_mapping=term_mapping,
        p2_binding_sha256=hashlib.sha256(_canonical(binding, code=code)).hexdigest(),
        executor_public_key=executor_key,
        observer_public_key=observer_key,
        maximum_lifetime_seconds=value.maximum_attestation_lifetime_seconds,
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_NONCANONICAL")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_NONCANONICAL")


def _parse_envelope(value: object, *, fields: frozenset[str], kind: str) -> dict[str, Any]:
    if type(value) is not bytes or not 1 <= len(value) <= _MAX_WIRE_BYTES:
        _fail(f"PHYSICAL_FULL_MATRIX_V4_FI_FENCE_{kind}_BYTES_INVALID")
    try:
        decoded = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        _fail(f"PHYSICAL_FULL_MATRIX_V4_FI_FENCE_{kind}_NONCANONICAL")
    if type(decoded) is not dict or value != _canonical(
        decoded, code=f"PHYSICAL_FULL_MATRIX_V4_FI_FENCE_{kind}_NONCANONICAL"
    ):
        _fail(f"PHYSICAL_FULL_MATRIX_V4_FI_FENCE_{kind}_NONCANONICAL")
    if set(decoded) != fields:
        _fail(f"PHYSICAL_FULL_MATRIX_V4_FI_FENCE_{kind}_FIELDS_INVALID")
    return dict(decoded)


def _signature(value: object, *, code: str) -> bytes:
    if type(value) is not str or not value or len(value) > 128:
        _fail(code)
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        _fail(code)
    if len(decoded) != 64:
        _fail(code)
    return decoded


def _verify_signature(
    *,
    item: dict[str, Any],
    public_key: bytes,
    domain: bytes,
    kind: str,
) -> dict[str, Any]:
    unsigned = dict(item)
    signature = _signature(
        unsigned.pop("signature", None),
        code=f"PHYSICAL_FULL_MATRIX_V4_FI_FENCE_{kind}_SIGNATURE_INVALID",
    )
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            domain
            + _canonical(
                unsigned,
                code=f"PHYSICAL_FULL_MATRIX_V4_FI_FENCE_{kind}_NONCANONICAL",
            ),
        )
    except (InvalidSignature, ValueError) as exc:
        raise PhysicalFullMatrixV4FiFenceScopeInstallationError(
            f"PHYSICAL_FULL_MATRIX_V4_FI_FENCE_{kind}_SIGNATURE_INVALID"
        ) from exc
    return unsigned


def _authority_flags_valid(item: Mapping[str, Any]) -> bool:
    return (
        item["writer_authorized"] is False
        and item["promotion_authorized"] is False
        and item["external_effect_authorized"] is False
        and item["installation_authorized"] is False
        and item["execution_authorized"] is False
        and item["full_matrix_authorized"] is False
    )


def _scope_policy_binding_sha256(*, role: str, facts: _ConfigFacts) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_SCHEMA,
                "scope_mode": _SCOPE_MODE,
                "signer_role": role,
                "p2_binding_sha256": facts.p2_binding_sha256,
                "mandatory_coverage": _REQUIRED_COVERAGE_CANONICAL,
                "writer_authorized": False,
                "promotion_authorized": False,
                "external_effect_authorized": False,
                "installation_authorized": False,
                "execution_authorized": False,
                "full_matrix_authorized": False,
            },
            code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_POLICY_BINDING_INVALID",
        )
    ).hexdigest()


def _installation_binding_sha256(
    *,
    role: str,
    facts: _ConfigFacts,
    scope_sha256: str,
    scope_binding_sha256: str,
    implementation_sha256: str,
    configuration_sha256: str,
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_SCHEMA,
                "signer_role": role,
                "p2_binding_sha256": facts.p2_binding_sha256,
                "scope_policy_sha256": scope_sha256,
                "scope_policy_binding_sha256": scope_binding_sha256,
                "installation_implementation_sha256": implementation_sha256,
                "installation_configuration_sha256": configuration_sha256,
                "writer_authorized": False,
                "promotion_authorized": False,
                "external_effect_authorized": False,
                "installation_authorized": False,
                "execution_authorized": False,
                "full_matrix_authorized": False,
            },
            code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_BINDING_INVALID",
        )
    ).hexdigest()


def _verify_scope_policy(
    *,
    raw: object,
    role: str,
    public_key: bytes,
    facts: _ConfigFacts,
) -> _ScopeFacts:
    item = _parse_envelope(raw, fields=_SCOPE_FIELDS, kind="SCOPE_POLICY")
    item = _verify_signature(
        item=item,
        public_key=public_key,
        domain=_ROLE_TO_DOMAIN[role],
        kind="SCOPE_POLICY",
    )
    if (
        item["schema"] != PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_POLICY_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != 1
        or item["status"] != _SCOPE_STATUS
        or item["scope_mode"] != _SCOPE_MODE
        or item["signer_role"] != role
        or item["signer_key_id"] != _key_id(public_key)
        or item["signature_algorithm"] != _SIGNATURE_ALGORITHM
        or item["p2_binding_sha256"] != facts.p2_binding_sha256
        or item["phase2_effect_start"] != facts.effect_start_mapping
        or item["phase2_effect_start_anchor"] != facts.effect_start_anchor_mapping
        or item["predecessor_term"] != facts.predecessor_term_mapping
        or not _authority_flags_valid(item)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_POLICY_BINDING_MISMATCH")
    # Do not accept a caller-selected policy hash, a partial coverage list, or
    # a generic arbitrary policy object in place of this exact coverage model.
    if item["mandatory_coverage"] != _REQUIRED_COVERAGE_CANONICAL:
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_POLICY_COVERAGE_INVALID")
    expected_binding = _scope_policy_binding_sha256(role=role, facts=facts)
    if (
        _sha256(
            item["scope_policy_binding_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_POLICY_BINDING_MISMATCH",
        )
        != expected_binding
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_POLICY_BINDING_MISMATCH")
    return _ScopeFacts(
        sha256=hashlib.sha256(raw).hexdigest(),
        binding_sha256=expected_binding,
    )


def _verify_installation_attestation(
    *,
    raw: object,
    role: str,
    public_key: bytes,
    scope: _ScopeFacts,
    facts: _ConfigFacts,
    now: datetime,
) -> _InstallationFacts:
    item = _parse_envelope(raw, fields=_INSTALLATION_FIELDS, kind="INSTALLATION_ATTESTATION")
    item = _verify_signature(
        item=item,
        public_key=public_key,
        domain=_ROLE_TO_INSTALLATION_DOMAIN[role],
        kind="INSTALLATION_ATTESTATION",
    )
    if (
        item["schema"] != PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != 1
        or item["status"] != _INSTALLATION_STATUS
        or item["signer_role"] != role
        or item["signer_key_id"] != _key_id(public_key)
        or item["signature_algorithm"] != _SIGNATURE_ALGORITHM
        or item["p2_binding_sha256"] != facts.p2_binding_sha256
        or item["scope_policy_sha256"] != scope.sha256
        or item["scope_policy_binding_sha256"] != scope.binding_sha256
        or not _authority_flags_valid(item)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_BINDING_MISMATCH")
    implementation = _sha256(
        item["installation_implementation_sha256"],
        code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_BINDING_MISMATCH",
    )
    configuration = _sha256(
        item["installation_configuration_sha256"],
        code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_BINDING_MISMATCH",
    )
    expected_binding = _installation_binding_sha256(
        role=role,
        facts=facts,
        scope_sha256=scope.sha256,
        scope_binding_sha256=scope.binding_sha256,
        implementation_sha256=implementation,
        configuration_sha256=configuration,
    )
    if (
        _sha256(
            item["installation_binding_sha256"],
            code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_BINDING_MISMATCH",
        )
        != expected_binding
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_BINDING_MISMATCH")
    installed_at = _parse_timestamp(
        item["installed_at"],
        code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_TIME_INVALID",
    )
    expires_at = _parse_timestamp(
        item["expires_at"],
        code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_TIME_INVALID",
    )
    if (
        expires_at <= installed_at
        or expires_at - installed_at
        > timedelta(seconds=facts.maximum_lifetime_seconds)
        or installed_at > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS)
        or now - installed_at > timedelta(seconds=facts.maximum_lifetime_seconds)
        or now > expires_at
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_INSTALLATION_ATTESTATION_STALE")
    return _InstallationFacts(
        sha256=hashlib.sha256(raw).hexdigest(),
        binding_sha256=expected_binding,
    )


def _provenance_body(
    *,
    facts: _ConfigFacts,
    executor_scope: _ScopeFacts,
    executor_installation: _InstallationFacts,
    observer_scope: _ScopeFacts,
    observer_installation: _InstallationFacts,
    verified_at: datetime,
) -> dict[str, object]:
    return {
        "schema": PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_SCHEMA,
        "status": PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_STATUS,
        "verified_at": _render_timestamp(
            verified_at,
            code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_TIME_INVALID",
        ),
        "p2_binding_sha256": facts.p2_binding_sha256,
        "executor_signer_key_id": _key_id(facts.executor_public_key),
        "observer_signer_key_id": _key_id(facts.observer_public_key),
        "executor_scope_policy_sha256": executor_scope.sha256,
        "executor_scope_policy_binding_sha256": executor_scope.binding_sha256,
        "executor_installation_attestation_sha256": executor_installation.sha256,
        "executor_installation_binding_sha256": executor_installation.binding_sha256,
        "observer_scope_policy_sha256": observer_scope.sha256,
        "observer_scope_policy_binding_sha256": observer_scope.binding_sha256,
        "observer_installation_attestation_sha256": observer_installation.sha256,
        "observer_installation_binding_sha256": observer_installation.binding_sha256,
        "writer_authorized": False,
        "promotion_authorized": False,
        "external_effect_authorized": False,
        "installation_authorized": False,
        "execution_authorized": False,
        "full_matrix_authorized": False,
    }


def _public_values(
    value: VerifiedPhysicalFullMatrixV4FiFenceScopeInstallationProvenance,
) -> tuple[object, ...]:
    return tuple(getattr(value, name) for name in _PUBLIC_FIELDS)


def verify_physical_full_matrix_v4_fi_fence_scope_installation_provenance(
    *,
    evidence: PhysicalFullMatrixV4FiFenceScopeInstallationEvidence,
    config: PhysicalFullMatrixV4FiFenceScopeInstallationVerificationConfig,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV4FiFenceScopeInstallationProvenance:
    """Verify the structured P2 FI-fence installation evidence, default-off.

    No receipt is an executable instruction.  Successful verification merely
    creates an opaque in-process evidence object from which callers can later
    project the six non-authorizing P2 evidence pins.
    """

    facts = _p2_facts(config)
    if type(evidence) is not PhysicalFullMatrixV4FiFenceScopeInstallationEvidence:
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_EVIDENCE_INVALID")
    checked_now = _utc(
        now, code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_TIME_INVALID"
    )
    executor_scope = _verify_scope_policy(
        raw=evidence.executor_scope_policy,
        role=_EXECUTOR_ROLE,
        public_key=facts.executor_public_key,
        facts=facts,
    )
    executor_installation = _verify_installation_attestation(
        raw=evidence.executor_installation_attestation,
        role=_EXECUTOR_ROLE,
        public_key=facts.executor_public_key,
        scope=executor_scope,
        facts=facts,
        now=checked_now,
    )
    observer_scope = _verify_scope_policy(
        raw=evidence.observer_scope_policy,
        role=_OBSERVER_ROLE,
        public_key=facts.observer_public_key,
        facts=facts,
    )
    observer_installation = _verify_installation_attestation(
        raw=evidence.observer_installation_attestation,
        role=_OBSERVER_ROLE,
        public_key=facts.observer_public_key,
        scope=observer_scope,
        facts=facts,
        now=checked_now,
    )
    # The role is part of the signed content, but spell out the separation
    # invariant too: no executor artifact can be relabelled as observer
    # provenance, and no single pin can cover both independent installations.
    if (
        executor_scope.sha256 == observer_scope.sha256
        or executor_installation.sha256 == observer_installation.sha256
        or executor_scope.sha256 == executor_installation.sha256
        or observer_scope.sha256 == observer_installation.sha256
        or executor_scope.sha256 == observer_installation.sha256
        or observer_scope.sha256 == executor_installation.sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_ROLE_PINS_NOT_DISTINCT")
    body = _provenance_body(
        facts=facts,
        executor_scope=executor_scope,
        executor_installation=executor_installation,
        observer_scope=observer_scope,
        observer_installation=observer_installation,
        verified_at=checked_now,
    )
    result = VerifiedPhysicalFullMatrixV4FiFenceScopeInstallationProvenance(
        schema=PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_SCHEMA,
        status=PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_STATUS,
        provenance_sha256=hashlib.sha256(
            _canonical(
                body,
                code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_INVALID",
            )
        ).hexdigest(),
        verified_at=checked_now,
        run_id=str(facts.effect_start.run_id),
        plan_sha256=facts.effect_start.plan_sha256,
        phase2_effect_start_identity_sha256=(
            facts.effect_start.journaled_effect_start_identity_sha256
        ),
        phase2_anchor_sequence=facts.effect_start_anchor.anchor_sequence,
        phase2_anchor_head_sha256=facts.effect_start_anchor.anchor_head_sha256,
        predecessor_writer_epoch=facts.predecessor_term.writer_epoch,
        predecessor_writer_lease_id=facts.predecessor_term.writer_lease_id,
        executor_signer_key_id=_key_id(facts.executor_public_key),
        observer_signer_key_id=_key_id(facts.observer_public_key),
        executor_scope_policy_sha256=executor_scope.sha256,
        executor_scope_policy_binding_sha256=executor_scope.binding_sha256,
        executor_installation_attestation_sha256=executor_installation.sha256,
        executor_installation_binding_sha256=executor_installation.binding_sha256,
        observer_scope_policy_sha256=observer_scope.sha256,
        observer_scope_policy_binding_sha256=observer_scope.binding_sha256,
        observer_installation_attestation_sha256=observer_installation.sha256,
        observer_installation_binding_sha256=observer_installation.binding_sha256,
        capability=_CAPABILITY,
    )
    _RESULT_STATES[result] = _ResultState(facts=facts, public_values=_public_values(result))
    return result


def require_verified_physical_full_matrix_v4_fi_fence_scope_installation_provenance(
    value: object,
    *,
    config: PhysicalFullMatrixV4FiFenceScopeInstallationVerificationConfig,
) -> VerifiedPhysicalFullMatrixV4FiFenceScopeInstallationProvenance:
    """Require the exact, untampered evidence-only provenance for ``config``."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixV4FiFenceScopeInstallationProvenance
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_REQUIRED")
    state = _RESULT_STATES.get(value)
    if state is None or state.public_values != _public_values(value):
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_TAMPERED")
    facts = _p2_facts(config)
    if (
        state.facts != facts
        or value.schema != PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_SCHEMA
        or value.status != PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_STATUS
        or value.run_id != str(facts.effect_start.run_id)
        or value.plan_sha256 != facts.effect_start.plan_sha256
        or value.phase2_effect_start_identity_sha256
        != facts.effect_start.journaled_effect_start_identity_sha256
        or value.phase2_anchor_sequence != facts.effect_start_anchor.anchor_sequence
        or value.phase2_anchor_head_sha256 != facts.effect_start_anchor.anchor_head_sha256
        or value.predecessor_writer_epoch != facts.predecessor_term.writer_epoch
        or value.predecessor_writer_lease_id != facts.predecessor_term.writer_lease_id
        or value.executor_signer_key_id != _key_id(facts.executor_public_key)
        or value.observer_signer_key_id != _key_id(facts.observer_public_key)
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.external_effect_authorized is not False
        or value.installation_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_MISMATCH")
    for digest in (
        value.provenance_sha256,
        value.executor_scope_policy_sha256,
        value.executor_scope_policy_binding_sha256,
        value.executor_installation_attestation_sha256,
        value.executor_installation_binding_sha256,
        value.observer_scope_policy_sha256,
        value.observer_scope_policy_binding_sha256,
        value.observer_installation_attestation_sha256,
        value.observer_installation_binding_sha256,
    ):
        _sha256(
            digest,
            code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_PROVENANCE_MISMATCH",
        )
    return value


def project_physical_full_matrix_v4_fi_fence_scope_installation_evidence_pins(
    value: object,
    *,
    executor_fence_evidence_sha256: str,
    observer_fence_evidence_sha256: str,
    config: PhysicalFullMatrixV4FiFenceScopeInstallationVerificationConfig,
) -> _p2.RetiredFiPredecessorFenceEvidencePins:
    """Project this verifier's pins into the existing P2 contract only.

    The two post-fence digests are intentionally not interpreted here: they
    belong to the future physical executor and independent observer evidence
    receipts.  They are checked only for shape and separation, then become
    correlation pins which P2's signed three-receipt verifier must repeat.
    """

    provenance = require_verified_physical_full_matrix_v4_fi_fence_scope_installation_provenance(
        value, config=config
    )
    executor_fence = _sha256(
        executor_fence_evidence_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_EVIDENCE_PINS_INVALID",
    )
    observer_fence = _sha256(
        observer_fence_evidence_sha256,
        code="PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_EVIDENCE_PINS_INVALID",
    )
    all_pins = (
        provenance.executor_installation_attestation_sha256,
        provenance.executor_scope_policy_sha256,
        executor_fence,
        provenance.observer_installation_attestation_sha256,
        provenance.observer_scope_policy_sha256,
        observer_fence,
    )
    if len(set(all_pins)) != len(all_pins):
        _fail("PHYSICAL_FULL_MATRIX_V4_FI_FENCE_SCOPE_INSTALLATION_EVIDENCE_PINS_NOT_DISTINCT")
    return _p2.RetiredFiPredecessorFenceEvidencePins(
        executor_installation_attestation_sha256=(
            provenance.executor_installation_attestation_sha256
        ),
        executor_scope_policy_sha256=provenance.executor_scope_policy_sha256,
        executor_fence_evidence_sha256=executor_fence,
        observer_installation_attestation_sha256=(
            provenance.observer_installation_attestation_sha256
        ),
        observer_scope_policy_sha256=provenance.observer_scope_policy_sha256,
        observer_fence_evidence_sha256=observer_fence,
    )
