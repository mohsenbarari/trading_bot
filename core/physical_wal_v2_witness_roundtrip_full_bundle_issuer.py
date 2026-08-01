"""Pure prepare/finalize issuance for the V2 public FullBundleAttestation.

This narrow boundary builds only the canonical signed public bundle consumed by
the role-local dispatcher.  It has no provider client, network, filesystem,
credential, peer-root, or installation surface.  A root-owned injected signer
receives one already-domain-separated byte payload and returns a signature; it
never exposes a private key through this API.

The request has eight *named* projection slots rather than a role selector or
role map.  Before the signer is invoked, the fixed topology, all common
binding/configuration pins, and the deployment-authority public-key hash are
validated and canonicalized.  Finalization verifies the returned signature
under the root-pinned public key, so a substituted signer cannot mint output.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core import physical_wal_v2_witness_roundtrip_arvan_s3v4_delivery_dispatcher as _bundle


__all__ = (
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_DEFAULT_ENABLED",
    "PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuanceRequest",
    "PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError",
    "PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection",
    "PhysicalWalV2WitnessRoundtripFullBundleAttestationSigningConfig",
    "PhysicalWalV2WitnessRoundtripFullBundleDeploymentSigner",
    "PreparedPhysicalWalV2WitnessRoundtripFullBundleAttestation",
    "finalize_prepared_physical_wal_v2_witness_roundtrip_full_bundle_attestation",
    "prepare_physical_wal_v2_witness_roundtrip_full_bundle_attestation",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_DEFAULT_ENABLED = False
_MAXIMUM_EVIDENCE_AGE_SECONDS = 86_400
_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RELEASE_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$", re.ASCII)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_UNSIGNED_FIELDS = _bundle._FULL_FIELDS - {"signature_base64"}
_CAPABILITY = object()


class PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError(ValueError):
    """The public full-bundle issuance request or injected signer is unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PhysicalWalV2WitnessRoundtripFullBundleDeploymentSigner(Protocol):
    """Root-owned deployment signer injection; no private-key accessor exists."""

    def sign_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
        self,
        *,
        signing_payload: bytes,
    ) -> bytes: ...


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection:
    """One public named role projection; it never contains config or secrets."""

    host_id: str = ""
    local_role: str = ""
    mailbox: str = ""
    direction: str = ""
    object_prefix: str = ""
    admission_sha256: str = ""
    retention_proof_sha256: str = ""
    provider_route_iam_attestation_sha256: str = ""


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuanceRequest:
    """Default-off request with exactly eight named public topology slots."""

    bundle_id: str = ""
    bundle_nonce: str = ""
    release_sha: str = ""
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    deployment_binding_sha256: str = ""
    deployment_authority_public_key_sha256: str = ""
    delivery_binding_sha256: str = ""
    roundtrip_configuration_sha256: str = ""
    fi_writer_source_outbox: PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection | None = field(
        default=None,
        repr=False,
    )
    witness_fi_ingress: PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection | None = field(
        default=None,
        repr=False,
    )
    witness_ir_egress: PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection | None = field(
        default=None,
        repr=False,
    )
    ir_standby_ack_inbox: PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection | None = field(
        default=None,
        repr=False,
    )
    ir_durable_ack_outbox: PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection | None = field(
        default=None,
        repr=False,
    )
    witness_ir_ingress: PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection | None = field(
        default=None,
        repr=False,
    )
    witness_fi_egress: PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection | None = field(
        default=None,
        repr=False,
    )
    fi_writer_ack_inbox: PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection | None = field(
        default=None,
        repr=False,
    )
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripFullBundleAttestationSigningConfig:
    """Root-pinned public deployment signer and exact common evidence pins."""

    deployment_authority_public_key: bytes | None = field(default=None, repr=False)
    expected_release_sha: str = ""
    expected_deployment_binding_sha256: str = ""
    expected_delivery_binding_sha256: str = ""
    expected_roundtrip_configuration_sha256: str = ""
    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = 300


@dataclass(frozen=True, init=False)
class PreparedPhysicalWalV2WitnessRoundtripFullBundleAttestation:
    """Non-forgeable canonical public payload awaiting root-owned signing."""

    bundle_id: str
    bundle_nonce: str
    release_sha: str
    issued_at: datetime
    expires_at: datetime
    deployment_binding_sha256: str
    deployment_authority_public_key_sha256: str
    delivery_binding_sha256: str
    roundtrip_configuration_sha256: str
    canonical_unsigned: bytes = field(repr=False)
    signing_payload: bytes = field(repr=False)
    _configuration_sha256: str = field(repr=False)
    _capability: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        bundle_id: str,
        bundle_nonce: str,
        release_sha: str,
        issued_at: datetime,
        expires_at: datetime,
        deployment_binding_sha256: str,
        deployment_authority_public_key_sha256: str,
        delivery_binding_sha256: str,
        roundtrip_configuration_sha256: str,
        canonical_unsigned: bytes,
        signing_payload: bytes,
        configuration_sha256: str,
        capability: object,
    ) -> None:
        if capability is not _CAPABILITY:
            raise TypeError("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_PREPARED_CONSTRUCTION_FORBIDDEN")
        for name, value in (
            ("bundle_id", bundle_id),
            ("bundle_nonce", bundle_nonce),
            ("release_sha", release_sha),
            ("issued_at", issued_at),
            ("expires_at", expires_at),
            ("deployment_binding_sha256", deployment_binding_sha256),
            ("deployment_authority_public_key_sha256", deployment_authority_public_key_sha256),
            ("delivery_binding_sha256", delivery_binding_sha256),
            ("roundtrip_configuration_sha256", roundtrip_configuration_sha256),
            ("canonical_unsigned", canonical_unsigned),
            ("signing_payload", signing_payload),
            ("_configuration_sha256", configuration_sha256),
            ("_capability", capability),
        ):
            object.__setattr__(self, name, value)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_PREPARED_SERIALIZATION_FORBIDDEN")


def _fail(code: str) -> None:
    raise PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError(code)


def _canonical(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError(code) from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _release(value: object, *, code: str) -> str:
    if type(value) is not str or _RELEASE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    result = value.astimezone(timezone.utc)
    if result.microsecond != 0:
        _fail(code)
    return result


def _timestamp(value: datetime, *, code: str) -> str:
    return _utc(value, code=code).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_DUPLICATE_FIELD")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID")


def _signing_context(
    value: object,
) -> tuple[Ed25519PublicKey, str, str, str, str, str, int]:
    if (
        type(value) is not PhysicalWalV2WitnessRoundtripFullBundleAttestationSigningConfig
        or value.enabled is not True
        or type(value.deployment_authority_public_key) is not bytes
        or len(value.deployment_authority_public_key) != 32
        or type(value.maximum_evidence_age_seconds) is not int
        or not 1 <= value.maximum_evidence_age_seconds <= _MAXIMUM_EVIDENCE_AGE_SECONDS
    ):
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_SIGNING_CONFIG_INVALID")
    try:
        authority = Ed25519PublicKey.from_public_bytes(value.deployment_authority_public_key)
    except ValueError as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_SIGNING_CONFIG_INVALID"
        ) from exc
    deployment_binding_sha256 = _sha256(
        value.expected_deployment_binding_sha256,
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_SIGNING_CONFIG_INVALID",
    )
    release_sha = _release(
        value.expected_release_sha,
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_SIGNING_CONFIG_INVALID",
    )
    delivery_binding_sha256 = _sha256(
        value.expected_delivery_binding_sha256,
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_SIGNING_CONFIG_INVALID",
    )
    roundtrip_configuration_sha256 = _sha256(
        value.expected_roundtrip_configuration_sha256,
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_SIGNING_CONFIG_INVALID",
    )
    configuration_sha256 = _sha256_bytes(
        _canonical(
            {
                "deployment_authority_public_key_sha256": _sha256_bytes(
                    value.deployment_authority_public_key
                ),
                "release_sha": release_sha,
                "deployment_binding_sha256": deployment_binding_sha256,
                "delivery_binding_sha256": delivery_binding_sha256,
                "roundtrip_configuration_sha256": roundtrip_configuration_sha256,
                "maximum_evidence_age_seconds": value.maximum_evidence_age_seconds,
            },
            code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_SIGNING_CONFIG_INVALID",
        )
    )
    return (
        authority,
        configuration_sha256,
        release_sha,
        deployment_binding_sha256,
        delivery_binding_sha256,
        roundtrip_configuration_sha256,
        value.maximum_evidence_age_seconds,
    )


def _projection(
    value: object,
    *,
    expected: tuple[str, str, str, str],
    deployment_binding_sha256: str,
    delivery_binding_sha256: str,
    roundtrip_configuration_sha256: str,
) -> dict[str, str]:
    if type(value) is not PhysicalWalV2WitnessRoundtripFullBundleAttestationPublicProjection:
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PROJECTION_INVALID")
    local_role, mailbox, direction, object_prefix = expected
    if (
        type(value.host_id) is not str
        or not value.host_id.isascii()
        or not 1 <= len(value.host_id) <= 127
        or value.local_role != local_role
        or value.mailbox != mailbox
        or value.direction != direction
        or value.object_prefix != object_prefix
    ):
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PROJECTION_INVALID")
    return {
        "host_id": value.host_id,
        "local_role": local_role,
        "mailbox": mailbox,
        "direction": direction,
        "object_prefix": object_prefix,
        "admission_sha256": _sha256(
            value.admission_sha256,
            code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PROJECTION_INVALID",
        ),
        "deployment_binding_sha256": deployment_binding_sha256,
        "delivery_binding_sha256": delivery_binding_sha256,
        "retention_proof_sha256": _sha256(
            value.retention_proof_sha256,
            code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PROJECTION_INVALID",
        ),
        "provider_route_iam_attestation_sha256": _sha256(
            value.provider_route_iam_attestation_sha256,
            code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PROJECTION_INVALID",
        ),
        "roundtrip_configuration_sha256": roundtrip_configuration_sha256,
    }


def _request_projections(
    value: PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuanceRequest,
    *,
    deployment_binding_sha256: str,
    delivery_binding_sha256: str,
    roundtrip_configuration_sha256: str,
) -> list[dict[str, str]]:
    return [
        _projection(
            value.fi_writer_source_outbox,
            expected=_bundle._ROLE_SPECS[0],
            deployment_binding_sha256=deployment_binding_sha256,
            delivery_binding_sha256=delivery_binding_sha256,
            roundtrip_configuration_sha256=roundtrip_configuration_sha256,
        ),
        _projection(
            value.witness_fi_ingress,
            expected=_bundle._ROLE_SPECS[1],
            deployment_binding_sha256=deployment_binding_sha256,
            delivery_binding_sha256=delivery_binding_sha256,
            roundtrip_configuration_sha256=roundtrip_configuration_sha256,
        ),
        _projection(
            value.witness_ir_egress,
            expected=_bundle._ROLE_SPECS[2],
            deployment_binding_sha256=deployment_binding_sha256,
            delivery_binding_sha256=delivery_binding_sha256,
            roundtrip_configuration_sha256=roundtrip_configuration_sha256,
        ),
        _projection(
            value.ir_standby_ack_inbox,
            expected=_bundle._ROLE_SPECS[3],
            deployment_binding_sha256=deployment_binding_sha256,
            delivery_binding_sha256=delivery_binding_sha256,
            roundtrip_configuration_sha256=roundtrip_configuration_sha256,
        ),
        _projection(
            value.ir_durable_ack_outbox,
            expected=_bundle._ROLE_SPECS[4],
            deployment_binding_sha256=deployment_binding_sha256,
            delivery_binding_sha256=delivery_binding_sha256,
            roundtrip_configuration_sha256=roundtrip_configuration_sha256,
        ),
        _projection(
            value.witness_ir_ingress,
            expected=_bundle._ROLE_SPECS[5],
            deployment_binding_sha256=deployment_binding_sha256,
            delivery_binding_sha256=delivery_binding_sha256,
            roundtrip_configuration_sha256=roundtrip_configuration_sha256,
        ),
        _projection(
            value.witness_fi_egress,
            expected=_bundle._ROLE_SPECS[6],
            deployment_binding_sha256=deployment_binding_sha256,
            delivery_binding_sha256=delivery_binding_sha256,
            roundtrip_configuration_sha256=roundtrip_configuration_sha256,
        ),
        _projection(
            value.fi_writer_ack_inbox,
            expected=_bundle._ROLE_SPECS[7],
            deployment_binding_sha256=deployment_binding_sha256,
            delivery_binding_sha256=delivery_binding_sha256,
            roundtrip_configuration_sha256=roundtrip_configuration_sha256,
        ),
    ]


def _request_unsigned(
    value: object,
    *,
    signing_config: PhysicalWalV2WitnessRoundtripFullBundleAttestationSigningConfig,
    now: datetime,
) -> tuple[dict[str, object], str, datetime, datetime]:
    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_CLOCK_INVALID")
    (
        _authority,
        configuration_sha256,
        expected_release_sha,
        expected_deployment_binding_sha256,
        expected_delivery_binding_sha256,
        expected_roundtrip_configuration_sha256,
        maximum_age,
    ) = _signing_context(signing_config)
    if (
        type(value) is not PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuanceRequest
        or value.enabled is not True
    ):
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_INVALID")
    if type(value.bundle_id) is not str or _ID_RE.fullmatch(value.bundle_id) is None:
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_INVALID")
    if type(value.bundle_nonce) is not str or _NONCE_RE.fullmatch(value.bundle_nonce) is None:
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_INVALID")
    release_sha = _release(
        value.release_sha,
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_INVALID",
    )
    deployment_binding_sha256 = _sha256(
        value.deployment_binding_sha256,
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_INVALID",
    )
    authority_key_sha256 = _sha256(
        value.deployment_authority_public_key_sha256,
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_INVALID",
    )
    delivery_binding_sha256 = _sha256(
        value.delivery_binding_sha256,
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_INVALID",
    )
    roundtrip_configuration_sha256 = _sha256(
        value.roundtrip_configuration_sha256,
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_INVALID",
    )
    if (
        release_sha != expected_release_sha
        or deployment_binding_sha256 != expected_deployment_binding_sha256
        or delivery_binding_sha256 != expected_delivery_binding_sha256
        or roundtrip_configuration_sha256 != expected_roundtrip_configuration_sha256
        or authority_key_sha256
        != _sha256_bytes(signing_config.deployment_authority_public_key)  # type: ignore[arg-type]
    ):
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_COMMON_PIN_MISMATCH")
    issued_at = _utc(
        value.issued_at,
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_TIME_INVALID",
    )
    expires_at = _utc(
        value.expires_at,
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_TIME_INVALID",
    )
    if (
        issued_at > observed
        or expires_at <= observed
        or expires_at <= issued_at
        or (observed - issued_at).total_seconds() > maximum_age
    ):
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_STALE")
    return (
        {
            "schema": _bundle._FULL_BUNDLE_SCHEMA,
            "version": _bundle._FULL_BUNDLE_VERSION,
            "bundle_id": value.bundle_id,
            "bundle_nonce": value.bundle_nonce,
            "release_sha": release_sha,
            "issued_at": _timestamp(
                issued_at,
                code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_TIME_INVALID",
            ),
            "expires_at": _timestamp(
                expires_at,
                code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_TIME_INVALID",
            ),
            "deployment_binding_sha256": deployment_binding_sha256,
            "deployment_authority_public_key_sha256": authority_key_sha256,
            "role_projections": _request_projections(
                value,
                deployment_binding_sha256=deployment_binding_sha256,
                delivery_binding_sha256=delivery_binding_sha256,
                roundtrip_configuration_sha256=roundtrip_configuration_sha256,
            ),
        },
        configuration_sha256,
        issued_at,
        expires_at,
    )


def _parse_prepared_unsigned(value: object) -> dict[str, Any]:
    if type(value) is not bytes or not 1 <= len(value) <= 256 * 1024:
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID")
    try:
        item = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID"
        ) from exc
    if (
        type(item) is not dict
        or set(item) != _UNSIGNED_FIELDS
        or _canonical(item, code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID")
        != value
    ):
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID")
    return dict(item)


def _validate_prepared_unsigned(
    item: dict[str, Any],
    *,
    signing_config: PhysicalWalV2WitnessRoundtripFullBundleAttestationSigningConfig,
    now: datetime,
) -> tuple[str, datetime, datetime, str, str, str, str, str]:
    """Reuse request validation through a named-slot reconstruction is unsafe.

    Prepared bytes are instead checked against the final wire grammar directly
    by the existing fixed-role projection validator, then against the
    root-pinned common signing context.
    """

    observed = _utc(now, code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_CLOCK_INVALID")
    (
        _authority,
        configuration_sha256,
        expected_release_sha,
        expected_deployment_binding_sha256,
        expected_delivery_binding_sha256,
        expected_roundtrip_configuration_sha256,
        maximum_age,
    ) = _signing_context(signing_config)
    if (
        item["schema"] != _bundle._FULL_BUNDLE_SCHEMA
        or type(item["version"]) is not int
        or item["version"] != _bundle._FULL_BUNDLE_VERSION
        or type(item["bundle_id"]) is not str
        or _ID_RE.fullmatch(item["bundle_id"]) is None
        or type(item["bundle_nonce"]) is not str
        or _NONCE_RE.fullmatch(item["bundle_nonce"]) is None
        or type(item["role_projections"]) is not list
        or len(item["role_projections"]) != len(_bundle._ROLE_SPECS)
    ):
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID")
    deployment_binding_sha256 = _sha256(
        item["deployment_binding_sha256"],
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID",
    )
    release_sha = _release(
        item["release_sha"],
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID",
    )
    authority_key_sha256 = _sha256(
        item["deployment_authority_public_key_sha256"],
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID",
    )
    try:
        projections = tuple(
            _bundle._projection(value, expected=expected)
            for value, expected in zip(item["role_projections"], _bundle._ROLE_SPECS, strict=True)
        )
    except _bundle.PhysicalWalV2WitnessRoundtripArvanS3v4DeliveryDispatcherError as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID"
        ) from exc
    if (
        release_sha != expected_release_sha
        or len({projection.local_role for projection in projections}) != len(_bundle._ROLE_SPECS)
        or len({projection.deployment_binding_sha256 for projection in projections}) != 1
        or len({projection.delivery_binding_sha256 for projection in projections}) != 1
        or len({projection.roundtrip_configuration_sha256 for projection in projections}) != 1
        or deployment_binding_sha256 != expected_deployment_binding_sha256
        or authority_key_sha256
        != _sha256_bytes(signing_config.deployment_authority_public_key)  # type: ignore[arg-type]
        or any(
            projection.deployment_binding_sha256 != deployment_binding_sha256
            or projection.delivery_binding_sha256 != expected_delivery_binding_sha256
            or projection.roundtrip_configuration_sha256
            != expected_roundtrip_configuration_sha256
            for projection in projections
        )
    ):
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_COMMON_PIN_MISMATCH")
    if (
        type(item["issued_at"]) is not str
        or _TIMESTAMP_RE.fullmatch(item["issued_at"]) is None
        or type(item["expires_at"]) is not str
        or _TIMESTAMP_RE.fullmatch(item["expires_at"]) is None
    ):
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID")
    try:
        issued_at = datetime.strptime(item["issued_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        expires_at = datetime.strptime(item["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID"
        ) from exc
    if (
        issued_at > observed
        or expires_at <= observed
        or expires_at <= issued_at
        or (observed - issued_at).total_seconds() > maximum_age
    ):
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_REQUEST_STALE")
    return (
        configuration_sha256,
        issued_at,
        expires_at,
        release_sha,
        deployment_binding_sha256,
        authority_key_sha256,
        expected_delivery_binding_sha256,
        expected_roundtrip_configuration_sha256,
    )


def prepare_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
    *,
    request: PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuanceRequest,
    signing_config: PhysicalWalV2WitnessRoundtripFullBundleAttestationSigningConfig,
    now: datetime,
) -> PreparedPhysicalWalV2WitnessRoundtripFullBundleAttestation:
    """Validate and canonicalize public evidence before the signer is touched."""

    unsigned, configuration_sha256, issued_at, expires_at = _request_unsigned(
        request,
        signing_config=signing_config,
        now=now,
    )
    canonical_unsigned = _canonical(
        unsigned,
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID",
    )
    _validate_prepared_unsigned(unsigned, signing_config=signing_config, now=now)
    return PreparedPhysicalWalV2WitnessRoundtripFullBundleAttestation(
        bundle_id=request.bundle_id,
        bundle_nonce=request.bundle_nonce,
        release_sha=request.release_sha,
        issued_at=issued_at,
        expires_at=expires_at,
        deployment_binding_sha256=request.deployment_binding_sha256,
        deployment_authority_public_key_sha256=request.deployment_authority_public_key_sha256,
        delivery_binding_sha256=request.delivery_binding_sha256,
        roundtrip_configuration_sha256=request.roundtrip_configuration_sha256,
        canonical_unsigned=canonical_unsigned,
        signing_payload=_bundle._FULL_BUNDLE_DOMAIN + canonical_unsigned,
        configuration_sha256=configuration_sha256,
        capability=_CAPABILITY,
    )


def finalize_prepared_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
    prepared: object,
    *,
    signer: PhysicalWalV2WitnessRoundtripFullBundleDeploymentSigner,
    signing_config: PhysicalWalV2WitnessRoundtripFullBundleAttestationSigningConfig,
    now: datetime,
) -> bytes:
    """Ask the injected root signer once, verify it, and emit canonical bytes."""

    item = _parse_prepared_unsigned(
        prepared.canonical_unsigned
        if type(prepared) is PreparedPhysicalWalV2WitnessRoundtripFullBundleAttestation
        else None
    )
    (
        configuration_sha256,
        issued_at,
        expires_at,
        release_sha,
        deployment_binding_sha256,
        authority_key_sha256,
        delivery_binding_sha256,
        roundtrip_configuration_sha256,
    ) = _validate_prepared_unsigned(item, signing_config=signing_config, now=now)
    if (
        type(prepared) is not PreparedPhysicalWalV2WitnessRoundtripFullBundleAttestation
        or prepared._capability is not _CAPABILITY
        or prepared._configuration_sha256 != configuration_sha256
        or prepared.bundle_id != item["bundle_id"]
        or prepared.bundle_nonce != item["bundle_nonce"]
        or prepared.release_sha != release_sha
        or prepared.issued_at != issued_at
        or prepared.expires_at != expires_at
        or prepared.deployment_binding_sha256 != deployment_binding_sha256
        or prepared.deployment_authority_public_key_sha256 != authority_key_sha256
        or prepared.delivery_binding_sha256 != delivery_binding_sha256
        or prepared.roundtrip_configuration_sha256 != roundtrip_configuration_sha256
        or prepared.signing_payload != _bundle._FULL_BUNDLE_DOMAIN + prepared.canonical_unsigned
    ):
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_PREPARED_INVALID")
    try:
        signature = signer.sign_physical_wal_v2_witness_roundtrip_full_bundle_attestation(
            signing_payload=prepared.signing_payload
        )
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_SIGNER_FAILED"
        ) from exc
    if type(signature) is not bytes or len(signature) != 64:
        _fail("V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_SIGNER_INVALID")
    (
        authority,
        _config_sha,
        _release_sha,
        _dep,
        _delivery,
        _roundtrip,
        _maximum_age,
    ) = _signing_context(signing_config)
    try:
        authority.verify(signature, prepared.signing_payload)
    except InvalidSignature as exc:
        raise PhysicalWalV2WitnessRoundtripFullBundleAttestationIssuerError(
            "V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_SIGNER_MISMATCH"
        ) from exc
    return _canonical(
        {
            **item,
            "signature_base64": base64.b64encode(signature).decode("ascii"),
        },
        code="V2_WITNESS_ROUNDTRIP_FULL_BUNDLE_ISSUER_FINALIZE_INVALID",
    )
