"""Fail-closed four-identity admission facts for the IR-to-FI data route.

This module is intentionally separate from the normal FI-to-IR Arvan
preflight.  A promoted IR writer must not reuse the FI publisher or IR
receiver credential, and a rebuilding FI standby must not reuse either normal
direction credential.  It therefore validates only public, redacted facts for
four distinct machine identities and one non-overlapping reverse route scope.

An enabled configuration additionally requires a current opaque four-role
live-IAM Witness *durable admission*.  Raw provider aggregate evidence is
admitted only through the root-owned Witness ledger before this boundary sees
it.  The provider-evidence digest in an observation is therefore derived only
from ``admission.gate`` after the durable admission has been revalidated; a
caller cannot choose or substitute it.  The admission bridge is imported
lazily to avoid a cycle with its pure evidence/gate dependencies.

It has no credential-file, SDK, network, provider, Docker, PostgreSQL, SSH,
or subprocess dependency.  A later root-owned four-role factory is responsible
for collecting the provider evidence and minting an observation for this
boundary; this module merely provides the exact local binding that publisher
and receiver runtimes can revalidate before they expose their injected seams.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from core.append_only_sync_delta_batch import CAMPAIGN_ID_RE, RELEASE_SHA_RE, canonical_json_bytes
from core import physical_arvan_s3_role_profiles as _role_profiles
from core.physical_wal_object_manifest import PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE


__all__ = (
    "DEFAULT_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_MAX_AGE_SECONDS",
    "PHYSICAL_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_DEFAULT_ENABLED",
    "PHYSICAL_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_OBSERVATION_SCHEMA",
    "PHYSICAL_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_SCHEMA",
    "PhysicalIrToFiObjectStorageFailbackBinding",
    "PhysicalIrToFiObjectStorageFailbackObservation",
    "PhysicalIrToFiObjectStorageFailbackPreflightConfig",
    "PhysicalIrToFiObjectStorageFailbackPreflightError",
    "VerifiedPhysicalIrToFiObjectStorageFailbackPreflight",
    "build_physical_ir_to_fi_object_storage_failback_observation",
    "require_verified_physical_ir_to_fi_object_storage_failback_preflight",
    "validate_physical_ir_to_fi_object_storage_failback_binding",
    "verify_physical_ir_to_fi_object_storage_failback_preflight",
)


PHYSICAL_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_SCHEMA = (
    "gold-trade-physical-ir-to-fi-object-storage-failback-preflight-v1"
)
PHYSICAL_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_OBSERVATION_SCHEMA = (
    "gold-trade-physical-ir-to-fi-object-storage-failback-observation-v1"
)
PHYSICAL_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_DEFAULT_ENABLED = False
DEFAULT_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_MAX_AGE_SECONDS = 120

_MAX_AGE_SECONDS = 300
_MAX_FUTURE_SKEW_SECONDS = 5
_SOURCE_SITE = "webapp_ir"
_DESTINATION_SITE = "webapp_fi"
_DIRECT_CONTROL = "forbidden"
_DESTINATION_INGEST = "pull-only"
_IDENTITY_FIELDS = (
    "fi_publisher_identity_sha256",
    "ir_receiver_identity_sha256",
    "ir_publisher_identity_sha256",
    "fi_receiver_identity_sha256",
)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_PROFILE_BY_FIELD = {
    "fi_publisher_identity_sha256": _role_profiles.ARVAN_S3_FI_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
    "ir_receiver_identity_sha256": _role_profiles.ARVAN_S3_IR_RECEIVER_EXACT_READONLY_PROFILE,
    "ir_publisher_identity_sha256": _role_profiles.ARVAN_S3_IR_PUBLISHER_IMMUTABLE_CREATE_ONLY_PROFILE,
    "fi_receiver_identity_sha256": _role_profiles.ARVAN_S3_FI_RECEIVER_EXACT_READONLY_PROFILE,
}
_OBSERVATION_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "release_sha",
        "source_site",
        "destination_site",
        "object_storage_namespace",
        "route_binding_sha256",
        "normal_route_scope_sha256",
        "reverse_route_scope_sha256",
        "provider_preflight_evidence_sha256",
        "fi_publisher_identity_sha256",
        "ir_receiver_identity_sha256",
        "ir_publisher_identity_sha256",
        "fi_receiver_identity_sha256",
        "identity_profiles",
        "observed_at",
        "direct_site_control",
        "destination_object_ingest",
        "evidence_sha256",
    }
)
_CAPABILITY = object()


class PhysicalIrToFiObjectStorageFailbackPreflightError(ValueError):
    """One fixed, redacted reason that reverse-route admission is unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalIrToFiObjectStorageFailbackBinding:
    """Expected public facts for exactly one promoted-IR-to-FI route.

    The two route-scope digests are supplied by the concrete four-role
    provisioning/preflight layer.  They keep normal FI-to-IR and reverse
    IR-to-FI IAM prefixes distinct without exposing a bucket, endpoint, raw
    prefix, credential, or object selector here.
    """

    campaign_id: str
    release_sha: str
    route_binding_sha256: str
    normal_route_scope_sha256: str
    reverse_route_scope_sha256: str
    fi_publisher_identity_sha256: str
    ir_receiver_identity_sha256: str
    ir_publisher_identity_sha256: str
    fi_receiver_identity_sha256: str
    object_storage_namespace: str = PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
    source_site: str = _SOURCE_SITE
    destination_site: str = _DESTINATION_SITE
    direct_site_control: str = _DIRECT_CONTROL
    destination_object_ingest: str = _DESTINATION_INGEST


@dataclass(frozen=True)
class PhysicalIrToFiObjectStorageFailbackPreflightConfig:
    """Default-off local policy for one fixed reverse-route proof.

    Disabled compatibility configurations may omit the live-IAM values, but
    an enabled configuration must supply both the opaque durable admission
    and its exact live-IAM evidence binding.  They are revalidated at every
    consume point.  A raw preflight gate is intentionally not an input to this
    configuration.
    """

    binding: PhysicalIrToFiObjectStorageFailbackBinding | None = None
    enabled: bool = PHYSICAL_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = (
        DEFAULT_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_MAX_AGE_SECONDS
    )
    four_role_projection_binding: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    four_role_live_iam_binding: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    four_role_live_iam_durable_admission: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class PhysicalIrToFiObjectStorageFailbackObservation:
    """Redacted four-role provider/preflight projection, never a client."""

    schema: str
    status: str
    campaign_id: str
    release_sha: str
    source_site: str
    destination_site: str
    object_storage_namespace: str
    route_binding_sha256: str
    normal_route_scope_sha256: str
    reverse_route_scope_sha256: str
    provider_preflight_evidence_sha256: str
    fi_publisher_identity_sha256: str
    ir_receiver_identity_sha256: str
    ir_publisher_identity_sha256: str
    fi_receiver_identity_sha256: str
    identity_profiles: tuple[tuple[str, str], ...]
    observed_at: datetime
    direct_site_control: str
    destination_object_ingest: str
    evidence_sha256: str


@dataclass(frozen=True)
class VerifiedPhysicalIrToFiObjectStorageFailbackPreflight:
    """Opaque revalidatable preflight, not a credential or execution permit."""

    observation: PhysicalIrToFiObjectStorageFailbackObservation
    binding: PhysicalIrToFiObjectStorageFailbackBinding
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_SERIALIZATION_FORBIDDEN")


def _fail(code: str) -> None:
    raise PhysicalIrToFiObjectStorageFailbackPreflightError(code)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _age(value: object, *, code: str) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_AGE_SECONDS:
        _fail(code)
    return value


def _binding(value: object) -> PhysicalIrToFiObjectStorageFailbackBinding:
    if type(value) is not PhysicalIrToFiObjectStorageFailbackBinding:
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_BINDING_INVALID")
    if (
        type(value.campaign_id) is not str
        or CAMPAIGN_ID_RE.fullmatch(value.campaign_id) is None
        or type(value.release_sha) is not str
        or RELEASE_SHA_RE.fullmatch(value.release_sha) is None
        or value.source_site != _SOURCE_SITE
        or value.destination_site != _DESTINATION_SITE
        or value.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or value.direct_site_control != _DIRECT_CONTROL
        or value.destination_object_ingest != _DESTINATION_INGEST
    ):
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_BINDING_INVALID")
    _sha256(value.route_binding_sha256, code="IR_TO_FI_FAILBACK_PREFLIGHT_BINDING_INVALID")
    normal_scope = _sha256(
        value.normal_route_scope_sha256,
        code="IR_TO_FI_FAILBACK_PREFLIGHT_BINDING_INVALID",
    )
    reverse_scope = _sha256(
        value.reverse_route_scope_sha256,
        code="IR_TO_FI_FAILBACK_PREFLIGHT_BINDING_INVALID",
    )
    if normal_scope == reverse_scope:
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_ROUTE_SCOPE_COLLISION")
    identities = tuple(
        _sha256(getattr(value, field), code="IR_TO_FI_FAILBACK_PREFLIGHT_IDENTITIES_INVALID")
        for field in _IDENTITY_FIELDS
    )
    if len(set(identities)) != len(identities):
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_IDENTITIES_NOT_SEPARATED")
    return value


def validate_physical_ir_to_fi_object_storage_failback_binding(
    binding: PhysicalIrToFiObjectStorageFailbackBinding,
) -> PhysicalIrToFiObjectStorageFailbackBinding:
    """Purely validate one exact four-role reverse-route policy.

    Concrete root-owned credential/client factories use this public helper to
    reject a malformed local policy before they can open a role credential.
    It has no provider, credential-file, or network effect.
    """

    return _binding(binding)


def _config(
    value: object,
    *,
    require_enabled: bool,
    now: datetime | None = None,
) -> tuple[
    PhysicalIrToFiObjectStorageFailbackBinding,
    int,
    object | None,
    object | None,
    object | None,
]:
    if type(value) is not PhysicalIrToFiObjectStorageFailbackPreflightConfig:
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_CONFIG_INVALID")
    if type(value.enabled) is not bool:
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_CONFIG_INVALID")
    if require_enabled and value.enabled is not True:
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_DISABLED")
    binding = _binding(value.binding)
    age = _age(
        value.maximum_evidence_age_seconds,
        code="IR_TO_FI_FAILBACK_PREFLIGHT_CONFIG_INVALID",
    )
    if not require_enabled:
        return binding, age, None, None, None
    observed_now = _utc(now, code="IR_TO_FI_FAILBACK_PREFLIGHT_CLOCK_INVALID")
    projection_binding = _require_four_role_projection_binding(
        value.four_role_projection_binding,
        binding=binding,
    )
    live_iam_durable_admission = _require_four_role_live_iam_durable_admission(
        value.four_role_live_iam_durable_admission,
        live_iam_binding=value.four_role_live_iam_binding,
        binding=binding,
        observed_at=observed_now,
    )
    return (
        binding,
        age,
        projection_binding,
        value.four_role_live_iam_binding,
        live_iam_durable_admission,
    )


def _require_four_role_projection_binding(
    value: object,
    *,
    binding: PhysicalIrToFiObjectStorageFailbackBinding,
) -> object:
    """Require the pure exact-profile binder without importing it at module load.

    The binder is a local compatibility gate, not a provider/IAM receipt.  A
    lazy import avoids a preflight/factory import cycle while preserving its
    mandatory use by any enabled reverse preflight configuration.
    """

    try:
        from core.physical_arvan_s3_four_role_preflight_binding import (
            require_verified_physical_arvan_s3_four_role_preflight_binding,
        )

        return require_verified_physical_arvan_s3_four_role_preflight_binding(
            value,
            binding=binding,
        )
    except Exception:
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_FOUR_ROLE_BINDING_REQUIRED")


def _require_four_role_live_iam_durable_admission(
    value: object,
    *,
    live_iam_binding: object,
    binding: PhysicalIrToFiObjectStorageFailbackBinding,
    observed_at: datetime,
) -> object:
    """Revalidate an opaque durable Witness admission without a load cycle.

    The bridge authenticates the aggregate against the root-owned durable
    nonce ledger before it mints the contained gate.  This preflight does not
    accept a raw gate, raw aggregate, or caller-selected provider digest.
    """

    try:
        from core.physical_arvan_s3_four_role_live_iam_durable_admission_bridge import (
            require_verified_physical_arvan_s3_four_role_live_iam_durable_admission,
        )

        return require_verified_physical_arvan_s3_four_role_live_iam_durable_admission(
            value,
            live_iam_binding=live_iam_binding,
            failback_binding=binding,
            observed_at=observed_at,
        )
    except Exception:
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_LIVE_IAM_DURABLE_ADMISSION_REQUIRED")


def _profiles(value: object, *, code: str) -> tuple[tuple[str, str], ...]:
    if type(value) is not tuple or len(value) != len(_IDENTITY_FIELDS):
        _fail(code)
    expected = tuple((field.removesuffix("_identity_sha256"), _PROFILE_BY_FIELD[field]) for field in _IDENTITY_FIELDS)
    if value != expected:
        _fail(code)
    return expected


def _mapping(
    *,
    binding: PhysicalIrToFiObjectStorageFailbackBinding,
    provider_preflight_evidence_sha256: str,
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_OBSERVATION_SCHEMA,
        "status": "four-role-route-observed",
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "source_site": _SOURCE_SITE,
        "destination_site": _DESTINATION_SITE,
        "object_storage_namespace": PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE,
        "route_binding_sha256": binding.route_binding_sha256,
        "normal_route_scope_sha256": binding.normal_route_scope_sha256,
        "reverse_route_scope_sha256": binding.reverse_route_scope_sha256,
        "provider_preflight_evidence_sha256": provider_preflight_evidence_sha256,
        "fi_publisher_identity_sha256": binding.fi_publisher_identity_sha256,
        "ir_receiver_identity_sha256": binding.ir_receiver_identity_sha256,
        "ir_publisher_identity_sha256": binding.ir_publisher_identity_sha256,
        "fi_receiver_identity_sha256": binding.fi_receiver_identity_sha256,
        "identity_profiles": [
            [field.removesuffix("_identity_sha256"), _PROFILE_BY_FIELD[field]]
            for field in _IDENTITY_FIELDS
        ],
        "observed_at": observed_at.isoformat(),
        "direct_site_control": _DIRECT_CONTROL,
        "destination_object_ingest": _DESTINATION_INGEST,
    }


def build_physical_ir_to_fi_object_storage_failback_observation(
    *,
    binding: PhysicalIrToFiObjectStorageFailbackBinding,
    four_role_projection_binding: object,
    four_role_live_iam_binding: object,
    four_role_live_iam_durable_admission: object,
    observed_at: datetime,
) -> PhysicalIrToFiObjectStorageFailbackObservation:
    """Build a canonical redacted reverse-route observation without I/O."""

    checked = _binding(binding)
    _require_four_role_projection_binding(
        four_role_projection_binding,
        binding=checked,
    )
    observed = _utc(observed_at, code="IR_TO_FI_FAILBACK_PREFLIGHT_OBSERVATION_INVALID")
    admission = _require_four_role_live_iam_durable_admission(
        four_role_live_iam_durable_admission,
        live_iam_binding=four_role_live_iam_binding,
        binding=checked,
        observed_at=observed,
    )
    payload = _mapping(
        binding=checked,
        provider_preflight_evidence_sha256=_sha256(
            getattr(getattr(admission, "gate", None), "aggregate_sha256", None),
            code="IR_TO_FI_FAILBACK_PREFLIGHT_LIVE_IAM_DURABLE_ADMISSION_REQUIRED",
        ),
        observed_at=observed,
    )
    try:
        payload["evidence_sha256"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError):  # pragma: no cover - all fields normalized above.
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_OBSERVATION_INVALID")
    return PhysicalIrToFiObjectStorageFailbackObservation(
        schema=payload["schema"],
        status=payload["status"],
        campaign_id=payload["campaign_id"],
        release_sha=payload["release_sha"],
        source_site=payload["source_site"],
        destination_site=payload["destination_site"],
        object_storage_namespace=payload["object_storage_namespace"],
        route_binding_sha256=payload["route_binding_sha256"],
        normal_route_scope_sha256=payload["normal_route_scope_sha256"],
        reverse_route_scope_sha256=payload["reverse_route_scope_sha256"],
        provider_preflight_evidence_sha256=payload["provider_preflight_evidence_sha256"],
        fi_publisher_identity_sha256=payload["fi_publisher_identity_sha256"],
        ir_receiver_identity_sha256=payload["ir_receiver_identity_sha256"],
        ir_publisher_identity_sha256=payload["ir_publisher_identity_sha256"],
        fi_receiver_identity_sha256=payload["fi_receiver_identity_sha256"],
        identity_profiles=tuple((item[0], item[1]) for item in payload["identity_profiles"]),
        observed_at=observed,
        direct_site_control=_DIRECT_CONTROL,
        destination_object_ingest=_DESTINATION_INGEST,
        evidence_sha256=payload["evidence_sha256"],
    )


def _observation(
    value: object,
    *,
    binding: PhysicalIrToFiObjectStorageFailbackBinding,
    live_iam_durable_admission: object,
    now: datetime,
    maximum_age_seconds: int,
) -> PhysicalIrToFiObjectStorageFailbackObservation:
    if type(value) is not PhysicalIrToFiObjectStorageFailbackObservation:
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_OBSERVATION_INVALID")
    observed = _utc(value.observed_at, code="IR_TO_FI_FAILBACK_PREFLIGHT_OBSERVATION_INVALID")
    if observed > now + timedelta(seconds=_MAX_FUTURE_SKEW_SECONDS) or now - observed > timedelta(
        seconds=maximum_age_seconds
    ):
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_STALE")
    if (
        value.schema != PHYSICAL_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_OBSERVATION_SCHEMA
        or value.status != "four-role-route-observed"
        or value.campaign_id != binding.campaign_id
        or value.release_sha != binding.release_sha
        or value.source_site != _SOURCE_SITE
        or value.destination_site != _DESTINATION_SITE
        or value.object_storage_namespace != PHYSICAL_WAL_FAILBACK_OBJECT_STORAGE_NAMESPACE
        or value.route_binding_sha256 != binding.route_binding_sha256
        or value.normal_route_scope_sha256 != binding.normal_route_scope_sha256
        or value.reverse_route_scope_sha256 != binding.reverse_route_scope_sha256
        or value.direct_site_control != _DIRECT_CONTROL
        or value.destination_object_ingest != _DESTINATION_INGEST
    ):
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_BINDING_MISMATCH")
    for field in _IDENTITY_FIELDS:
        if getattr(value, field) != getattr(binding, field):
            _fail("IR_TO_FI_FAILBACK_PREFLIGHT_IDENTITIES_INVALID")
    _profiles(value.identity_profiles, code="IR_TO_FI_FAILBACK_PREFLIGHT_IDENTITIES_INVALID")
    provider_digest = _sha256(
        getattr(
            getattr(live_iam_durable_admission, "gate", None),
            "aggregate_sha256",
            None,
        ),
        code="IR_TO_FI_FAILBACK_PREFLIGHT_LIVE_IAM_DURABLE_ADMISSION_REQUIRED",
    )
    if value.provider_preflight_evidence_sha256 != provider_digest:
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_LIVE_IAM_DURABLE_ADMISSION_DIGEST_MISMATCH")
    payload = _mapping(
        binding=binding,
        provider_preflight_evidence_sha256=provider_digest,
        observed_at=observed,
    )
    try:
        expected_digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    except (TypeError, ValueError):  # pragma: no cover - normalized above.
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_OBSERVATION_INVALID")
    if _sha256(value.evidence_sha256, code="IR_TO_FI_FAILBACK_PREFLIGHT_OBSERVATION_INVALID") != expected_digest:
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_OBSERVATION_TAMPERED")
    return value


def verify_physical_ir_to_fi_object_storage_failback_preflight(
    observation: PhysicalIrToFiObjectStorageFailbackObservation,
    *,
    binding: PhysicalIrToFiObjectStorageFailbackBinding,
    four_role_projection_binding: object,
    four_role_live_iam_binding: object,
    four_role_live_iam_durable_admission: object,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_IR_TO_FI_OBJECT_STORAGE_FAILBACK_PREFLIGHT_MAX_AGE_SECONDS,
) -> VerifiedPhysicalIrToFiObjectStorageFailbackPreflight:
    """Verify one fresh four-role reverse-route observation without I/O."""

    checked_binding = _binding(binding)
    _require_four_role_projection_binding(
        four_role_projection_binding,
        binding=checked_binding,
    )
    observed_now = _utc(now, code="IR_TO_FI_FAILBACK_PREFLIGHT_CLOCK_INVALID")
    admission = _require_four_role_live_iam_durable_admission(
        four_role_live_iam_durable_admission,
        live_iam_binding=four_role_live_iam_binding,
        binding=checked_binding,
        observed_at=observed_now,
    )
    checked = _observation(
        observation,
        binding=checked_binding,
        live_iam_durable_admission=admission,
        now=observed_now,
        maximum_age_seconds=_age(
            maximum_evidence_age_seconds,
            code="IR_TO_FI_FAILBACK_PREFLIGHT_CONFIG_INVALID",
        ),
    )
    result = VerifiedPhysicalIrToFiObjectStorageFailbackPreflight(
        observation=checked,
        binding=checked_binding,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def require_verified_physical_ir_to_fi_object_storage_failback_preflight(
    value: object,
    *,
    config: PhysicalIrToFiObjectStorageFailbackPreflightConfig,
    now: datetime,
) -> VerifiedPhysicalIrToFiObjectStorageFailbackPreflight:
    """Require current reverse preflight under an enabled local policy."""

    (
        binding,
        maximum_age,
        four_role_projection_binding,
        live_iam_binding,
        live_iam_durable_admission,
    ) = _config(
        config,
        require_enabled=True,
        now=now,
    )
    assert four_role_projection_binding is not None
    assert live_iam_binding is not None
    assert live_iam_durable_admission is not None
    if (
        type(value) is not VerifiedPhysicalIrToFiObjectStorageFailbackPreflight
        or value._capability is not _CAPABILITY
        or value.binding != binding
    ):
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_REQUIRED")
    checked = verify_physical_ir_to_fi_object_storage_failback_preflight(
        value.observation,
        binding=binding,
        four_role_projection_binding=four_role_projection_binding,
        four_role_live_iam_binding=live_iam_binding,
        four_role_live_iam_durable_admission=live_iam_durable_admission,
        now=now,
        maximum_evidence_age_seconds=maximum_age,
    )
    if checked.observation != value.observation or checked.binding != value.binding:
        _fail("IR_TO_FI_FAILBACK_PREFLIGHT_TAMPERED")
    return value
