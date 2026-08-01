"""Pure readiness bridge from verified four-role live-IAM evidence to preflight.

The live-IAM protocol and the existing reverse preflight deliberately have
different responsibilities.  The former verifies signed provider observations
and consumes a durable Witness nonce; the latter owns public route-policy
facts.  This narrow gate joins only those already-verified public facts.

It accepts neither a provider response nor a caller-selected evidence hash.
In particular, a consumer cannot substitute an arbitrary value for the
aggregate receipt digest: the digest enters the gate only from the opaque
``VerifiedPhysicalArvanS3FourRoleLiveIamWitnessAggregate`` verifier result.
There is no credential, client, filesystem, SDK, network, subprocess, or
preflight execution behavior here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re

from core import physical_arvan_s3_four_role_live_iam_evidence as _live_iam
from core import physical_ir_to_fi_object_storage_failback_preflight as _failback


__all__ = (
    "PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_PREFLIGHT_GATE_SCHEMA",
    "PhysicalArvanS3FourRoleLiveIamPreflightGateError",
    "VerifiedPhysicalArvanS3FourRoleLiveIamPreflightGate",
    "mint_physical_arvan_s3_four_role_live_iam_preflight_gate",
    "require_verified_physical_arvan_s3_four_role_live_iam_preflight_gate",
)


PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_PREFLIGHT_GATE_SCHEMA = (
    "gold-trade-physical-arvan-s3-four-role-live-iam-preflight-gate-v1"
)

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_CAPABILITY = object()


class PhysicalArvanS3FourRoleLiveIamPreflightGateError(ValueError):
    """A live-IAM aggregate cannot satisfy the exact public preflight pin."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VerifiedPhysicalArvanS3FourRoleLiveIamPreflightGate:
    """Opaque, freshness-bound bridge result for a later readiness adapter.

    The fields are intentionally all redacted public facts: exact route and
    identity commitments, Witness nonce/expiry, and hashes of the verified
    aggregate.  It carries neither credential material nor a raw provider
    permission/preflight hash, and is not an execution permit.
    """

    schema: str
    campaign_id: str
    release_sha: str
    normal_route_scope_sha256: str
    reverse_route_scope_sha256: str
    four_role_route_binding_sha256: str
    fi_publisher_identity_sha256: str
    ir_receiver_identity_sha256: str
    ir_publisher_identity_sha256: str
    fi_receiver_identity_sha256: str
    evidence_binding_sha256: str
    witness_nonce: str
    witness_nonce_commitment_sha256: str
    aggregate_sha256: str
    expires_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("ARVAN_S3_FOUR_ROLE_LIVE_IAM_PREFLIGHT_GATE_SERIALIZATION_FORBIDDEN")


def _fail(code: str) -> None:
    raise PhysicalArvanS3FourRoleLiveIamPreflightGateError(code)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _failback_binding(value: object) -> _failback.PhysicalIrToFiObjectStorageFailbackBinding:
    if type(value) is not _failback.PhysicalIrToFiObjectStorageFailbackBinding:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_FAILBACK_BINDING_INVALID")
    try:
        return _failback.validate_physical_ir_to_fi_object_storage_failback_binding(value)
    except _failback.PhysicalIrToFiObjectStorageFailbackPreflightError:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_FAILBACK_BINDING_INVALID")


def _matching_facts(
    *,
    live_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
) -> None:
    if (
        live_binding.campaign_id != failback_binding.campaign_id
        or live_binding.release_sha != failback_binding.release_sha
        or live_binding.normal_route_scope_sha256 != failback_binding.normal_route_scope_sha256
        or live_binding.reverse_route_scope_sha256 != failback_binding.reverse_route_scope_sha256
        or live_binding.four_role_binding_sha256 != failback_binding.route_binding_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_ROUTE_BINDING_MISMATCH")
    if (
        live_binding.fi_publisher_identity_sha256 != failback_binding.fi_publisher_identity_sha256
        or live_binding.ir_receiver_identity_sha256 != failback_binding.ir_receiver_identity_sha256
        or live_binding.ir_publisher_identity_sha256 != failback_binding.ir_publisher_identity_sha256
        or live_binding.fi_receiver_identity_sha256 != failback_binding.fi_receiver_identity_sha256
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_IDENTITY_MISMATCH")


def _require_gate_shape(
    value: object,
    *,
    live_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    observed_at: object,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamPreflightGate:
    if (
        type(value) is not VerifiedPhysicalArvanS3FourRoleLiveIamPreflightGate
        or value._capability is not _CAPABILITY
        or value.schema != PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_PREFLIGHT_GATE_SCHEMA
    ):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_NOT_VERIFIED")
    _matching_facts(live_binding=live_binding, failback_binding=failback_binding)
    expected = {
        "campaign_id": live_binding.campaign_id,
        "release_sha": live_binding.release_sha,
        "normal_route_scope_sha256": live_binding.normal_route_scope_sha256,
        "reverse_route_scope_sha256": live_binding.reverse_route_scope_sha256,
        "four_role_route_binding_sha256": live_binding.four_role_binding_sha256,
        "fi_publisher_identity_sha256": live_binding.fi_publisher_identity_sha256,
        "ir_receiver_identity_sha256": live_binding.ir_receiver_identity_sha256,
        "ir_publisher_identity_sha256": live_binding.ir_publisher_identity_sha256,
        "fi_receiver_identity_sha256": live_binding.fi_receiver_identity_sha256,
        "evidence_binding_sha256": live_binding.evidence_binding_sha256,
    }
    if any(getattr(value, field) != expected_value for field, expected_value in expected.items()):
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_FACTS_MISMATCH")
    _sha256(value.witness_nonce_commitment_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_FACTS_MISMATCH")
    _sha256(value.aggregate_sha256, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_FACTS_MISMATCH")
    # The nonce grammar is owned by the live-IAM verifier.  Keep this gate
    # independent of its private constants while refusing a non-redacted form.
    if type(value.witness_nonce) is not str or _HEX64_RE.fullmatch(value.witness_nonce) is None or value.witness_nonce == "0" * 64:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_FACTS_MISMATCH")
    now = _utc(observed_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_TIME_INVALID")
    expires_at = _utc(value.expires_at, code="ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_FACTS_MISMATCH")
    if now >= expires_at:
        _fail("ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_STALE")
    return value


def mint_physical_arvan_s3_four_role_live_iam_preflight_gate(
    *,
    aggregate: _live_iam.VerifiedPhysicalArvanS3FourRoleLiveIamWitnessAggregate,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamPreflightGate:
    """Mint a gate only from a currently fresh, opaque verified aggregate.

    This intentionally calls the live-IAM require helper rather than checking
    the aggregate fields directly.  A forged dataclass, ``replace`` result,
    stale aggregate, or aggregate from another four-role route fails before a
    gate is created.
    """

    checked_failback = _failback_binding(failback_binding)
    try:
        checked_aggregate = _live_iam.require_verified_physical_arvan_s3_four_role_live_iam_witness_aggregate(
            aggregate,
            binding=live_iam_binding,
            observed_at=observed_at,
        )
    except _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceError as exc:
        _fail(f"ARVAN_S3_FOUR_ROLE_LIVE_IAM_GATE_AGGREGATE_{exc.code}")
    _matching_facts(live_binding=live_iam_binding, failback_binding=checked_failback)
    gate = VerifiedPhysicalArvanS3FourRoleLiveIamPreflightGate(
        schema=PHYSICAL_ARVAN_S3_FOUR_ROLE_LIVE_IAM_PREFLIGHT_GATE_SCHEMA,
        campaign_id=live_iam_binding.campaign_id,
        release_sha=live_iam_binding.release_sha,
        normal_route_scope_sha256=live_iam_binding.normal_route_scope_sha256,
        reverse_route_scope_sha256=live_iam_binding.reverse_route_scope_sha256,
        four_role_route_binding_sha256=live_iam_binding.four_role_binding_sha256,
        fi_publisher_identity_sha256=live_iam_binding.fi_publisher_identity_sha256,
        ir_receiver_identity_sha256=live_iam_binding.ir_receiver_identity_sha256,
        ir_publisher_identity_sha256=live_iam_binding.ir_publisher_identity_sha256,
        fi_receiver_identity_sha256=live_iam_binding.fi_receiver_identity_sha256,
        evidence_binding_sha256=live_iam_binding.evidence_binding_sha256,
        witness_nonce=checked_aggregate.nonce,
        witness_nonce_commitment_sha256=checked_aggregate.nonce_commitment_sha256,
        aggregate_sha256=checked_aggregate.raw_sha256,
        expires_at=checked_aggregate.expires_at,
    )
    object.__setattr__(gate, "_capability", _CAPABILITY)
    return _require_gate_shape(
        gate,
        live_binding=live_iam_binding,
        failback_binding=checked_failback,
        observed_at=observed_at,
    )


def require_verified_physical_arvan_s3_four_role_live_iam_preflight_gate(
    value: object,
    *,
    live_iam_binding: _live_iam.PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
    failback_binding: _failback.PhysicalIrToFiObjectStorageFailbackBinding,
    observed_at: datetime,
) -> VerifiedPhysicalArvanS3FourRoleLiveIamPreflightGate:
    """Revalidate the opaque, nonserializable gate before readiness use."""

    return _require_gate_shape(
        value,
        live_binding=live_iam_binding,
        failback_binding=_failback_binding(failback_binding),
        observed_at=observed_at,
    )
