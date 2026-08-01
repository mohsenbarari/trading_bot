"""Pure, read-only composition binding for a verified WA-FI release identity.

The signed descriptor verifier establishes the immutable intended release
identity.  A future root-owned runtime attester must independently observe the
release roots, Compose file digest, and loaded image identities.  This module
only compares those already supplied values.  It does not open a path, hash a
file, inspect Docker, read an environment variable, or authorize any action.

In particular, a successful binding is *evidence of equality*, not a writer
lease, promotion permit, deployment approval, or Full-Matrix result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Final
from weakref import WeakKeyDictionary

from core import fenced_fi_release_identity as _identity


__all__ = (
    "FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_SCHEMA",
    "FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_STATUS",
    "FencedFiReleaseIdentityRuntimeBinding",
    "FencedFiReleaseIdentityRuntimeBindingError",
    "FencedFiReleaseIdentityRuntimeObservations",
    "bind_fenced_fi_release_identity_runtime",
    "require_bound_fenced_fi_release_identity_runtime",
)


FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_SCHEMA: Final = (
    "gold-trade-wa-fi-fenced-release-identity-runtime-binding-v1"
)
FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_STATUS: Final = "equality-evidence-only"
_CAPABILITY: Final = object()


class FencedFiReleaseIdentityRuntimeBindingError(ValueError):
    """One injected runtime observation does not equal the signed identity."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise FencedFiReleaseIdentityRuntimeBindingError(code)


@dataclass(frozen=True)
class FencedFiReleaseIdentityRuntimeObservations:
    """Claims from a future local attester; this type performs no observation."""

    application_release_root: str
    control_release_root: str
    compose_relative_path: str
    compose_sha256: str
    app_image_repo_digest: str
    app_image_id: str
    bot_image_repo_digest: str
    bot_image_id: str


@dataclass(frozen=True, eq=False)
class FencedFiReleaseIdentityRuntimeBinding:
    """Opaque equality evidence, deliberately non-authorizing."""

    schema: str
    status: str
    binding_sha256: str
    identity_sha256: str
    application_release_root: str
    control_release_root: str
    compose_relative_path: str
    compose_sha256: str
    app_image_repo_digest: str
    app_image_id: str
    bot_image_repo_digest: str
    bot_image_id: str
    writer_authorized: bool = False
    promotion_authorized: bool = False
    deployment_authorized: bool = False
    execution_authorized: bool = False
    full_matrix_authorized: bool = False
    full_matrix_executed: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_COPY_FORBIDDEN")


_STATES: WeakKeyDictionary[
    FencedFiReleaseIdentityRuntimeBinding, tuple[_identity.FencedFiReleaseIdentity, FencedFiReleaseIdentityRuntimeObservations]
] = WeakKeyDictionary()


def _identity_value(value: object) -> _identity.FencedFiReleaseIdentity:
    try:
        value = _identity.require_verified_fenced_fi_release_identity(value)
    except _identity.FencedFiReleaseIdentityError as exc:
        _fail("FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_IDENTITY_INVALID")
    # The upstream verifier already validates every individual representation.
    # Repeat the semantic safety flags here so a hand-built lookalike cannot be
    # reinterpreted as authorization through this evidence-only boundary.
    if (
        value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_IDENTITY_INVALID")
    return value


def _binding_material(
    identity: _identity.FencedFiReleaseIdentity,
    observations: FencedFiReleaseIdentityRuntimeObservations,
) -> dict[str, str]:
    return {
        "schema": FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_SCHEMA,
        "status": FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_STATUS,
        "identity_sha256": identity.identity_sha256,
        "application_release_root": observations.application_release_root,
        "control_release_root": observations.control_release_root,
        "compose_relative_path": observations.compose_relative_path,
        "compose_sha256": observations.compose_sha256,
        "app_image_repo_digest": observations.app_image_repo_digest,
        "app_image_id": observations.app_image_id,
        "bot_image_repo_digest": observations.bot_image_repo_digest,
        "bot_image_id": observations.bot_image_id,
    }


def _observations(value: object) -> FencedFiReleaseIdentityRuntimeObservations:
    if type(value) is not FencedFiReleaseIdentityRuntimeObservations:
        _fail("FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_OBSERVATIONS_INVALID")
    for field_name in (
        "application_release_root",
        "control_release_root",
        "compose_relative_path",
        "compose_sha256",
        "app_image_repo_digest",
        "app_image_id",
        "bot_image_repo_digest",
        "bot_image_id",
    ):
        if type(getattr(value, field_name)) is not str:
            _fail("FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_OBSERVATIONS_INVALID")
    return value


def _equal(observed: str, expected: str, *, code: str) -> None:
    if observed != expected:
        _fail(code)


def bind_fenced_fi_release_identity_runtime(
    identity: _identity.FencedFiReleaseIdentity,
    *,
    observations: FencedFiReleaseIdentityRuntimeObservations,
) -> FencedFiReleaseIdentityRuntimeBinding:
    """Bind one verified identity to exact, caller-supplied local observations.

    This function intentionally cannot turn a matching descriptor into an
    action permit.  It has no filesystem, Docker, subprocess, or network I/O.
    """

    verified = _identity_value(identity)
    observed = _observations(observations)
    _equal(
        observed.application_release_root,
        verified.application_release_root,
        code="FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_APPLICATION_ROOT_MISMATCH",
    )
    _equal(
        observed.control_release_root,
        verified.control_release_root,
        code="FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_CONTROL_ROOT_MISMATCH",
    )
    _equal(
        observed.compose_relative_path,
        verified.compose_relative_path,
        code="FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_COMPOSE_PATH_MISMATCH",
    )
    _equal(
        observed.compose_sha256,
        verified.compose_sha256,
        code="FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_COMPOSE_SHA256_MISMATCH",
    )
    _equal(
        observed.app_image_repo_digest,
        verified.app_image_repo_digest,
        code="FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_APP_REPO_DIGEST_MISMATCH",
    )
    _equal(
        observed.app_image_id,
        verified.app_image_id,
        code="FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_APP_IMAGE_ID_MISMATCH",
    )
    _equal(
        observed.bot_image_repo_digest,
        verified.bot_image_repo_digest,
        code="FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_BOT_REPO_DIGEST_MISMATCH",
    )
    _equal(
        observed.bot_image_id,
        verified.bot_image_id,
        code="FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_BOT_IMAGE_ID_MISMATCH",
    )
    binding_material = _binding_material(verified, observed)
    binding = FencedFiReleaseIdentityRuntimeBinding(
        schema=FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_SCHEMA,
        status=FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_STATUS,
        binding_sha256=hashlib.sha256(
            _identity.canonical_fenced_fi_release_identity_json_bytes(binding_material)
        ).hexdigest(),
        identity_sha256=verified.identity_sha256,
        application_release_root=observed.application_release_root,
        control_release_root=observed.control_release_root,
        compose_relative_path=observed.compose_relative_path,
        compose_sha256=observed.compose_sha256,
        app_image_repo_digest=observed.app_image_repo_digest,
        app_image_id=observed.app_image_id,
        bot_image_repo_digest=observed.bot_image_repo_digest,
        bot_image_id=observed.bot_image_id,
    )
    object.__setattr__(binding, "_capability", _CAPABILITY)
    _STATES[binding] = (verified, observed)
    return binding


def require_bound_fenced_fi_release_identity_runtime(
    value: object,
) -> FencedFiReleaseIdentityRuntimeBinding:
    """Accept only evidence produced by this process; still never authorize it."""

    if (
        type(value) is not FencedFiReleaseIdentityRuntimeBinding
        or value._capability is not _CAPABILITY
        or value not in _STATES
        or value.schema != FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_SCHEMA
        or value.status != FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_STATUS
        or value.writer_authorized is not False
        or value.promotion_authorized is not False
        or value.deployment_authorized is not False
        or value.execution_authorized is not False
        or value.full_matrix_authorized is not False
        or value.full_matrix_executed is not False
    ):
        _fail("FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_INVALID")
    identity, observations = _STATES[value]
    try:
        verified = _identity.require_verified_fenced_fi_release_identity(identity)
    except _identity.FencedFiReleaseIdentityError:
        _fail("FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_INVALID")
    expected = _binding_material(verified, observations)
    if (
        value.identity_sha256 != expected["identity_sha256"]
        or value.application_release_root != expected["application_release_root"]
        or value.control_release_root != expected["control_release_root"]
        or value.compose_relative_path != expected["compose_relative_path"]
        or value.compose_sha256 != expected["compose_sha256"]
        or value.app_image_repo_digest != expected["app_image_repo_digest"]
        or value.app_image_id != expected["app_image_id"]
        or value.bot_image_repo_digest != expected["bot_image_repo_digest"]
        or value.bot_image_id != expected["bot_image_id"]
        or value.binding_sha256
        != hashlib.sha256(
            _identity.canonical_fenced_fi_release_identity_json_bytes(expected)
        ).hexdigest()
    ):
        _fail("FENCED_FI_RELEASE_IDENTITY_RUNTIME_BINDING_INVALID")
    return value
