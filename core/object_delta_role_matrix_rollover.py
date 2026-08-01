"""Pure, default-off term-rollover contract for the two-WebApp role matrix.

``core.object_delta_role_matrix`` deliberately validates a *static* pair of
source/receiver routes.  That is useful for a reviewed configuration, but a
plain ``ObjectDeltaRoleMatrixWriterTerm`` and a plain route object do not
prove that a promotion or failback is a new, Witness-authorized transition.
In particular, a stale receiver permit can otherwise be paired with an old
source pin after a role change.

This module adds the missing local admission boundary without enabling any
runtime path.  It accepts only:

* an opaque, signature-verified Writer Witness term proof;
* an opaque, source-signature-verified pair of route-generation artifacts;
* an opaque prior activation whose lineage is checked before a rollover.

The active route's permit and signed source-cutover evidence must both match
the fresh Witness term.  The inactive route must be byte-for-byte continuous
from the prior activation.  Therefore a promotion can replace only the
promoted route and a failback can replace only the normal route; neither can
silently introduce an old counterpart artifact.

No function here opens a file, contacts the Witness, reads a database, starts
a service, changes DNS, or writes state.  Verification of a signed proof is
not a live Witness query.  A future root-only coordinator must obtain the
proof from the live Witness, securely pin its public key, serialize and
durably consume activation lineage, atomically install the matching source
pin/receiver permit, and re-check live Witness state immediately before it
enables a writer.  A stateless pure function cannot stop two coordinators from
replaying the *same still-live inputs* concurrently; the carried lineage
rejects replay relative to a verified predecessor, while durable global
single-consumption remains that future adapter's responsibility.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from core.append_only_sync_delta_batch import LEASE_ID_RE, WEBAPP_SITES, canonical_json_bytes
from core.object_delta_role_matrix import (
    OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
    OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER,
    OBJECT_DELTA_ROLE_MATRIX_MODES,
    ObjectDeltaRoleMatrixError,
    ObjectDeltaRoleMatrixRoute,
    ObjectDeltaRoleMatrixWriterTerm,
    VerifiedObjectDeltaRoleMatrix,
    active_object_delta_role_matrix_route,
    authorize_object_delta_role_matrix,
    object_delta_role_matrix_site_role,
    require_verified_object_delta_role_matrix,
)
from core.object_delta_source_cutover_attestation import (
    ObjectDeltaSourceCutoverAttestationError,
    VerifiedObjectDeltaSourceCutoverAttestation,
    canonical_object_delta_source_cutover_attestation_bytes,
    parse_object_delta_source_cutover_attestation_json,
    verify_object_delta_source_cutover_attestation,
)


OBJECT_DELTA_ROLE_MATRIX_ROLLOVER_SCHEMA = "gold-trade-object-delta-role-matrix-rollover-v1"
OBJECT_DELTA_ROLE_MATRIX_ROLLOVER_DEFAULT_ENABLED = False
OBJECT_DELTA_ROLE_MATRIX_WITNESS_TERM_PROOF_VERSION = 1
OBJECT_DELTA_ROLE_MATRIX_WITNESS_TERM_AUTHORITY = "webapp"

# These bounds deliberately match the already-reviewed Writer Witness lease
# envelope bounds.  A future adapter may choose a narrower bound but cannot
# weaken this contract by passing an unbounded proof lifetime.
MIN_WITNESS_TERM_SAFETY_MARGIN_SECONDS = 1
MAX_WITNESS_TERM_SAFETY_MARGIN_SECONDS = 60
MIN_WITNESS_TERM_DURATION_SECONDS = 2
MAX_WITNESS_TERM_DURATION_SECONDS = 300
MAX_WITNESS_TERM_FUTURE_SKEW_SECONDS = 5

_WITNESS_TRANSITION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_VERIFIED_WITNESSED_TERM_CAPABILITY = object()
_VERIFIED_ROUTE_GENERATIONS_CAPABILITY = object()
_VERIFIED_ACTIVATION_CAPABILITY = object()


class ObjectDeltaRoleMatrixRolloverError(ValueError):
    """A term transition, route generation, or opaque capability is unsafe."""


@dataclass(frozen=True)
class VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    """Opaque, signature-verified Witness term evidence.

    The canonical proof is retained solely so each later use can re-check its
    signature, bounded lifetime, and field projection.  Direct construction
    and ``dataclasses.replace`` do not mint authority.
    """

    canonical_proof: bytes
    witness_public_key: bytes
    maximum_lease_duration_seconds: int
    safety_margin_seconds: int
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    issued_at: datetime
    expires_at: datetime
    proof_sha256: str
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedObjectDeltaRoleMatrixRouteGenerations:
    """Opaque pair of signed route-generation artifacts.

    Each raw source cutover is canonicalized and verified against its own
    root-pinned source pin.  The cutover's term must match its receiver
    permit, which is the binding absent from the static route type alone.
    """

    normal_route: ObjectDeltaRoleMatrixRoute
    normal_source_cutover_attestation: bytes
    promoted_route: ObjectDeltaRoleMatrixRoute
    promoted_source_cutover_attestation: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class ObjectDeltaRoleMatrixActivationRecord:
    """Non-secret lineage entry used to reject stale terms and artifacts."""

    active_mode: str
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    stream_generation_id: str
    source_cutover_attestation_sha256: str
    receiver_permit_sha256: str
    route_artifact_sha256: str


@dataclass(frozen=True)
class VerifiedObjectDeltaRoleMatrixActivation:
    """Opaque activation lineage; only active-role projection is public API.

    The private fields intentionally retain enough evidence to revalidate the
    current selection and to carry monotonic term/artifact history into the
    next pure rollover.  They do not activate a route by themselves.
    """

    _matrix: VerifiedObjectDeltaRoleMatrix
    _witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    _route_generations: VerifiedObjectDeltaRoleMatrixRouteGenerations
    _history: tuple[ObjectDeltaRoleMatrixActivationRecord, ...]
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _WitnessTermFacts:
    canonical_proof: bytes
    witness_public_key: bytes
    maximum_lease_duration_seconds: int
    safety_margin_seconds: int
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    issued_at: datetime
    expires_at: datetime
    proof_sha256: str


@dataclass(frozen=True)
class _RouteGenerationFacts:
    normal_route: ObjectDeltaRoleMatrixRoute
    normal_raw: bytes
    normal_cutover: VerifiedObjectDeltaSourceCutoverAttestation
    promoted_route: ObjectDeltaRoleMatrixRoute
    promoted_raw: bytes
    promoted_cutover: VerifiedObjectDeltaSourceCutoverAttestation


@dataclass(frozen=True)
class _ActivationFacts:
    matrix: VerifiedObjectDeltaRoleMatrix
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    routes: VerifiedObjectDeltaRoleMatrixRouteGenerations
    route_facts: _RouteGenerationFacts
    record: ObjectDeltaRoleMatrixActivationRecord


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ObjectDeltaRoleMatrixRolloverError(
                "Witness term proof contains duplicate JSON fields"
            )
        result[key] = value
    return result


def _utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} is invalid")
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} is invalid")
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Mapping[str, Any], *, label: str) -> bytes:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} is not canonical JSON") from exc


def _decode_base64(value: object, *, label: str, expected_bytes: int) -> bytes:
    if not isinstance(value, str):
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} is invalid") from exc
    if len(decoded) != expected_bytes:
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} is invalid")
    return decoded


def _validate_witness_public_key(value: object) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        raise ObjectDeltaRoleMatrixRolloverError("Witness public key is invalid")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError) as exc:
        raise ObjectDeltaRoleMatrixRolloverError("Witness public key is invalid") from exc
    return value


def _validate_term_bounds(
    *,
    maximum_lease_duration_seconds: object,
    safety_margin_seconds: object,
) -> tuple[int, int]:
    if (
        type(maximum_lease_duration_seconds) is not int
        or not MIN_WITNESS_TERM_DURATION_SECONDS
        <= maximum_lease_duration_seconds
        <= MAX_WITNESS_TERM_DURATION_SECONDS
    ):
        raise ObjectDeltaRoleMatrixRolloverError("Witness maximum lease duration is invalid")
    if (
        type(safety_margin_seconds) is not int
        or not MIN_WITNESS_TERM_SAFETY_MARGIN_SECONDS
        <= safety_margin_seconds
        <= MAX_WITNESS_TERM_SAFETY_MARGIN_SECONDS
        or safety_margin_seconds >= maximum_lease_duration_seconds
    ):
        raise ObjectDeltaRoleMatrixRolloverError("Witness term safety margin is invalid")
    return maximum_lease_duration_seconds, safety_margin_seconds


def _parse_witness_proof(value: object) -> tuple[dict[str, Any], bytes]:
    if isinstance(value, Mapping):
        proof = dict(value)
        raw = _canonical_json(proof, label="Witness term proof")
    elif isinstance(value, bytes):
        raw = value
        try:
            proof = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_strict_object)
        except ObjectDeltaRoleMatrixRolloverError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ObjectDeltaRoleMatrixRolloverError("Witness term proof is invalid JSON") from exc
        if not isinstance(proof, dict):
            raise ObjectDeltaRoleMatrixRolloverError("Witness term proof is invalid JSON")
        if _canonical_json(proof, label="Witness term proof") != raw:
            raise ObjectDeltaRoleMatrixRolloverError("Witness term proof is not canonical")
    else:
        raise ObjectDeltaRoleMatrixRolloverError("Witness term proof is invalid")
    return proof, raw


def _validate_witness_term(
    proof_value: object,
    *,
    witness_public_key: object,
    maximum_lease_duration_seconds: object,
    safety_margin_seconds: object,
    now: object,
    require_live: bool,
) -> _WitnessTermFacts:
    proof, canonical_proof = _parse_witness_proof(proof_value)
    expected_fields = {
        "version",
        "authority",
        "holder_site",
        "writer_epoch",
        "lease_id",
        "issued_at",
        "expires_at",
        "witness_transition_id",
        "signature",
    }
    if set(proof) != expected_fields:
        raise ObjectDeltaRoleMatrixRolloverError("Witness term proof fields are invalid")
    if (
        proof["version"] != OBJECT_DELTA_ROLE_MATRIX_WITNESS_TERM_PROOF_VERSION
        or proof["authority"] != OBJECT_DELTA_ROLE_MATRIX_WITNESS_TERM_AUTHORITY
    ):
        raise ObjectDeltaRoleMatrixRolloverError("Witness term proof protocol is invalid")
    holder_site = proof["holder_site"]
    if holder_site not in WEBAPP_SITES:
        raise ObjectDeltaRoleMatrixRolloverError("Witness term holder site is invalid")
    writer_epoch = proof["writer_epoch"]
    if type(writer_epoch) is not int or writer_epoch < 1:
        raise ObjectDeltaRoleMatrixRolloverError("Witness term epoch is invalid")
    writer_lease_id = proof["lease_id"]
    if not isinstance(writer_lease_id, str) or LEASE_ID_RE.fullmatch(writer_lease_id) is None:
        raise ObjectDeltaRoleMatrixRolloverError("Witness term lease ID is invalid")
    transition_id = proof["witness_transition_id"]
    if not isinstance(transition_id, str) or _WITNESS_TRANSITION_ID_RE.fullmatch(transition_id) is None:
        raise ObjectDeltaRoleMatrixRolloverError("Witness term transition ID is invalid")
    issued_at = _parse_timestamp(proof["issued_at"], label="Witness term issue time")
    expires_at = _parse_timestamp(proof["expires_at"], label="Witness term expiry")
    maximum, margin = _validate_term_bounds(
        maximum_lease_duration_seconds=maximum_lease_duration_seconds,
        safety_margin_seconds=safety_margin_seconds,
    )
    if expires_at <= issued_at or expires_at - issued_at > timedelta(seconds=maximum):
        raise ObjectDeltaRoleMatrixRolloverError("Witness term proof lifetime is invalid")
    observed_at = _utc(now, label="Witness term verification clock")
    if issued_at > observed_at + timedelta(seconds=MAX_WITNESS_TERM_FUTURE_SKEW_SECONDS):
        raise ObjectDeltaRoleMatrixRolloverError("Witness term proof is issued in the future")
    if require_live and expires_at <= observed_at + timedelta(seconds=margin):
        raise ObjectDeltaRoleMatrixRolloverError("Witness term proof is expired or too close to expiry")
    pinned_key = _validate_witness_public_key(witness_public_key)
    unsigned_fields = (
        "version",
        "authority",
        "holder_site",
        "writer_epoch",
        "lease_id",
        "issued_at",
        "expires_at",
        "witness_transition_id",
    )
    unsigned = {field: proof[field] for field in unsigned_fields}
    signature = _decode_base64(
        proof["signature"],
        label="Witness term proof signature",
        expected_bytes=64,
    )
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(pinned_key).verify(
            signature,
            _canonical_json(unsigned, label="Witness term proof"),
        )
    except (InvalidSignature, ValueError) as exc:
        raise ObjectDeltaRoleMatrixRolloverError("Witness term proof signature is invalid") from exc
    return _WitnessTermFacts(
        canonical_proof=canonical_proof,
        witness_public_key=pinned_key,
        maximum_lease_duration_seconds=maximum,
        safety_margin_seconds=margin,
        holder_site=holder_site,
        writer_epoch=writer_epoch,
        writer_lease_id=writer_lease_id,
        witness_transition_id=transition_id,
        issued_at=issued_at,
        expires_at=expires_at,
        proof_sha256=hashlib.sha256(canonical_proof).hexdigest(),
    )


def build_object_delta_role_matrix_witnessed_term_proof(
    *,
    holder_site: str,
    writer_epoch: int,
    writer_lease_id: str,
    witness_transition_id: str,
    issued_at: datetime,
    expires_at: datetime,
    witness_signer: object,
) -> dict[str, Any]:
    """Build a deterministic signed Witness-proof fixture or adapter artifact.

    This helper performs no key loading.  Production callers must give it only
    a signer held by the Writer Witness; normal coordinators should receive an
    already-signed proof and call the verifier below instead.
    """

    try:
        from cryptography.hazmat.primitives import serialization

        witness_public_key = witness_signer.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (AttributeError, ImportError, TypeError, ValueError) as exc:
        raise ObjectDeltaRoleMatrixRolloverError("Witness signer is invalid") from exc
    unsigned = {
        "version": OBJECT_DELTA_ROLE_MATRIX_WITNESS_TERM_PROOF_VERSION,
        "authority": OBJECT_DELTA_ROLE_MATRIX_WITNESS_TERM_AUTHORITY,
        "holder_site": holder_site,
        "writer_epoch": writer_epoch,
        "lease_id": writer_lease_id,
        "issued_at": _utc(issued_at, label="Witness term issue time").isoformat(),
        "expires_at": _utc(expires_at, label="Witness term expiry").isoformat(),
        "witness_transition_id": witness_transition_id,
    }
    try:
        signature = witness_signer.sign(_canonical_json(unsigned, label="Witness term proof"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ObjectDeltaRoleMatrixRolloverError("Witness signer cannot sign term proof") from exc
    if not isinstance(signature, bytes) or len(signature) != 64:
        raise ObjectDeltaRoleMatrixRolloverError("Witness signer produced an invalid signature")
    proof = {
        **unsigned,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    # Round-trip every field and the newly created signature before returning
    # the evidence, while keeping the helper side-effect free.
    _validate_witness_term(
        proof,
        witness_public_key=witness_public_key,
        maximum_lease_duration_seconds=MAX_WITNESS_TERM_DURATION_SECONDS,
        safety_margin_seconds=MIN_WITNESS_TERM_SAFETY_MARGIN_SECONDS,
        now=issued_at,
        require_live=False,
    )
    return proof


def verify_object_delta_role_matrix_witnessed_term(
    proof: Mapping[str, Any] | bytes,
    *,
    witness_public_key: bytes,
    maximum_lease_duration_seconds: int,
    safety_margin_seconds: int,
    now: datetime,
) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    """Verify a signed, currently live Witness proof and mint opaque evidence.

    A raw ``ObjectDeltaRoleMatrixWriterTerm`` is intentionally not accepted.
    The future live-Witness adapter is responsible for securely acquiring this
    proof and for its final just-before-enable liveness check.
    """

    facts = _validate_witness_term(
        proof,
        witness_public_key=witness_public_key,
        maximum_lease_duration_seconds=maximum_lease_duration_seconds,
        safety_margin_seconds=safety_margin_seconds,
        now=now,
        require_live=True,
    )
    result = VerifiedObjectDeltaRoleMatrixWitnessedTerm(
        canonical_proof=facts.canonical_proof,
        witness_public_key=facts.witness_public_key,
        maximum_lease_duration_seconds=facts.maximum_lease_duration_seconds,
        safety_margin_seconds=facts.safety_margin_seconds,
        holder_site=facts.holder_site,
        writer_epoch=facts.writer_epoch,
        writer_lease_id=facts.writer_lease_id,
        witness_transition_id=facts.witness_transition_id,
        issued_at=facts.issued_at,
        expires_at=facts.expires_at,
        proof_sha256=facts.proof_sha256,
    )
    object.__setattr__(result, "_capability", _VERIFIED_WITNESSED_TERM_CAPABILITY)
    _validated_witnessed_term(result, now=now, require_live=True)
    return result


def _validated_witnessed_term(
    value: object,
    *,
    now: datetime,
    require_live: bool,
) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    if type(value) is not VerifiedObjectDeltaRoleMatrixWitnessedTerm:
        raise ObjectDeltaRoleMatrixRolloverError("verified Witness term capability is required")
    if value._capability is not _VERIFIED_WITNESSED_TERM_CAPABILITY:
        raise ObjectDeltaRoleMatrixRolloverError("verified Witness term was not authorized")
    facts = _validate_witness_term(
        value.canonical_proof,
        witness_public_key=value.witness_public_key,
        maximum_lease_duration_seconds=value.maximum_lease_duration_seconds,
        safety_margin_seconds=value.safety_margin_seconds,
        now=now,
        require_live=require_live,
    )
    expected = (
        facts.canonical_proof,
        facts.witness_public_key,
        facts.maximum_lease_duration_seconds,
        facts.safety_margin_seconds,
        facts.holder_site,
        facts.writer_epoch,
        facts.writer_lease_id,
        facts.witness_transition_id,
        facts.issued_at,
        facts.expires_at,
        facts.proof_sha256,
    )
    actual = (
        value.canonical_proof,
        value.witness_public_key,
        value.maximum_lease_duration_seconds,
        value.safety_margin_seconds,
        value.holder_site,
        value.writer_epoch,
        value.writer_lease_id,
        value.witness_transition_id,
        value.issued_at,
        value.expires_at,
        value.proof_sha256,
    )
    if actual != expected:
        raise ObjectDeltaRoleMatrixRolloverError("verified Witness term is not normalized")
    return value


def require_verified_object_delta_role_matrix_witnessed_term(
    value: object,
    *,
    now: datetime,
) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    """Revalidate signed term evidence without requiring an unexpired lease.

    Rollover needs to inspect an expired predecessor so failover can happen
    after its former holder loses authority.  New/current activation always
    uses the live-only helper below.
    """

    return _validated_witnessed_term(value, now=now, require_live=False)


def require_live_object_delta_role_matrix_witnessed_term(
    value: object,
    *,
    now: datetime,
) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    """Require that an opaque Witness proof remains live with its margin."""

    return _validated_witnessed_term(value, now=now, require_live=True)


def _canonical_cutover(value: object, *, label: str) -> bytes:
    try:
        if isinstance(value, Mapping):
            return canonical_object_delta_source_cutover_attestation_bytes(value)
        if isinstance(value, (bytes, str)):
            parsed = parse_object_delta_source_cutover_attestation_json(value)
            return canonical_object_delta_source_cutover_attestation_bytes(parsed)
    except ObjectDeltaSourceCutoverAttestationError as exc:
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} source cutover is invalid") from exc
    raise ObjectDeltaRoleMatrixRolloverError(f"{label} source cutover is invalid")


def _route_cutover(
    route: ObjectDeltaRoleMatrixRoute,
    raw: bytes,
    *,
    label: str,
) -> VerifiedObjectDeltaSourceCutoverAttestation:
    pin = route.source_pin
    binding = pin.binding
    try:
        verified = verify_object_delta_source_cutover_attestation(
            raw,
            expected_source_public_key=pin.expected_source_public_key,
            expected_source_site=binding.source_site,
            expected_destination_site=binding.destination_site,
            expected_campaign_id=binding.campaign_id,
            expected_release_sha=binding.release_sha,
            expected_stream_generation_id=binding.stream_generation_id,
            expected_registry_fingerprint=binding.expected_registry_fingerprint,
        )
    except (AttributeError, ObjectDeltaSourceCutoverAttestationError, ValueError) as exc:
        raise ObjectDeltaRoleMatrixRolloverError(
            f"{label} source cutover does not match its route pin"
        ) from exc
    permit = route.receiver_binding.permit
    if (verified.writer_epoch, verified.writer_lease_id) != (
        permit.writer_epoch,
        permit.writer_lease_id,
    ):
        raise ObjectDeltaRoleMatrixRolloverError(
            f"{label} source cutover does not match its receiver permit term"
        )
    return verified


def _route_generation_facts(
    *,
    normal_route: object,
    normal_source_cutover_attestation: object,
    promoted_route: object,
    promoted_source_cutover_attestation: object,
) -> _RouteGenerationFacts:
    # First route values pass through the existing static matrix normalizer.
    # The normal term is only a validation input here; it is never activation
    # authority and the later activation derives its term from an opaque
    # signed Witness proof.
    try:
        normal_permit = normal_route.receiver_binding.permit
        normal_term = ObjectDeltaRoleMatrixWriterTerm(
            holder_site="webapp_fi",
            writer_epoch=normal_permit.writer_epoch,
            writer_lease_id=normal_permit.writer_lease_id,
        )
        static = authorize_object_delta_role_matrix(
            normal_route=normal_route,
            promoted_route=promoted_route,
            active_mode=OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER,
            active_writer_term=normal_term,
        )
    except (AttributeError, ObjectDeltaRoleMatrixError, TypeError, ValueError) as exc:
        raise ObjectDeltaRoleMatrixRolloverError(
            "route generation binding is not a valid two-direction role matrix"
        ) from exc
    normal = static.normal_route
    promoted = static.promoted_route
    normal_raw = _canonical_cutover(normal_source_cutover_attestation, label="normal route")
    promoted_raw = _canonical_cutover(promoted_source_cutover_attestation, label="promoted route")
    normal_cutover = _route_cutover(normal, normal_raw, label="normal route")
    promoted_cutover = _route_cutover(promoted, promoted_raw, label="promoted route")
    return _RouteGenerationFacts(
        normal_route=normal,
        normal_raw=normal_raw,
        normal_cutover=normal_cutover,
        promoted_route=promoted,
        promoted_raw=promoted_raw,
        promoted_cutover=promoted_cutover,
    )


def verify_object_delta_role_matrix_route_generations(
    *,
    normal_route: ObjectDeltaRoleMatrixRoute,
    normal_source_cutover_attestation: Mapping[str, Any] | bytes | str,
    promoted_route: ObjectDeltaRoleMatrixRoute,
    promoted_source_cutover_attestation: Mapping[str, Any] | bytes | str,
) -> VerifiedObjectDeltaRoleMatrixRouteGenerations:
    """Verify fresh source-cutover/permit bindings for both directions.

    The input route objects are configuration claims, not authority.  Only the
    returned opaque value may feed bootstrap or a role rollover.
    """

    facts = _route_generation_facts(
        normal_route=normal_route,
        normal_source_cutover_attestation=normal_source_cutover_attestation,
        promoted_route=promoted_route,
        promoted_source_cutover_attestation=promoted_source_cutover_attestation,
    )
    result = VerifiedObjectDeltaRoleMatrixRouteGenerations(
        normal_route=facts.normal_route,
        normal_source_cutover_attestation=facts.normal_raw,
        promoted_route=facts.promoted_route,
        promoted_source_cutover_attestation=facts.promoted_raw,
    )
    object.__setattr__(result, "_capability", _VERIFIED_ROUTE_GENERATIONS_CAPABILITY)
    _validated_route_generations(result)
    return result


def _validated_route_generations(
    value: object,
) -> tuple[VerifiedObjectDeltaRoleMatrixRouteGenerations, _RouteGenerationFacts]:
    if type(value) is not VerifiedObjectDeltaRoleMatrixRouteGenerations:
        raise ObjectDeltaRoleMatrixRolloverError("verified route-generation capability is required")
    if value._capability is not _VERIFIED_ROUTE_GENERATIONS_CAPABILITY:
        raise ObjectDeltaRoleMatrixRolloverError("verified route generations were not authorized")
    facts = _route_generation_facts(
        normal_route=value.normal_route,
        normal_source_cutover_attestation=value.normal_source_cutover_attestation,
        promoted_route=value.promoted_route,
        promoted_source_cutover_attestation=value.promoted_source_cutover_attestation,
    )
    if (
        value.normal_route != facts.normal_route
        or value.normal_source_cutover_attestation != facts.normal_raw
        or value.promoted_route != facts.promoted_route
        or value.promoted_source_cutover_attestation != facts.promoted_raw
    ):
        raise ObjectDeltaRoleMatrixRolloverError("verified route generations are not normalized")
    return value, facts


def require_verified_object_delta_role_matrix_route_generations(
    value: object,
) -> VerifiedObjectDeltaRoleMatrixRouteGenerations:
    """Revalidate opaque source-cutover/permit route evidence."""

    routes, _facts = _validated_route_generations(value)
    return routes


def _permit_sha256(route: ObjectDeltaRoleMatrixRoute) -> str:
    source = route.source_pin.binding
    permit = route.receiver_binding.permit
    policy = route.receiver_binding.policy
    payload = {
        "source": {
            "source_site": source.source_site,
            "destination_site": source.destination_site,
            "campaign_id": source.campaign_id,
            "release_sha": source.release_sha,
            "stream_generation_id": source.stream_generation_id,
            "expected_registry_fingerprint": source.expected_registry_fingerprint,
            "source_public_key_sha256": hashlib.sha256(
                route.source_pin.expected_source_public_key
            ).hexdigest(),
        },
        "permit": {
            "source_site": permit.source_site,
            "destination_site": permit.destination_site,
            "campaign_id": permit.campaign_id,
            "release_sha": permit.release_sha,
            "stream_generation_id": permit.stream_generation_id,
            "bucket": permit.bucket,
            "destination_age_recipient": permit.destination_age_recipient,
            "controller_key_id": permit.controller_key_id,
            "writer_epoch": permit.writer_epoch,
            "writer_lease_id": permit.writer_lease_id,
        },
        "policy": {
            "bucket": policy.bucket,
            "prefix": policy.prefix,
            "webapp_fi_age_recipient": policy.webapp_fi_age_recipient,
            "webapp_ir_age_recipient": policy.webapp_ir_age_recipient,
        },
        "receiver": {
            "source_key_id": route.receiver_binding.source_key_id,
            "controller_public_key_sha256": hashlib.sha256(
                route.receiver_binding.controller_public_key
            ).hexdigest(),
            "expected_registry_fingerprint": route.receiver_binding.expected_registry_fingerprint,
        },
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _record_for(
    *,
    matrix: VerifiedObjectDeltaRoleMatrix,
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    route_facts: _RouteGenerationFacts,
) -> ObjectDeltaRoleMatrixActivationRecord:
    active_route = active_object_delta_role_matrix_route(matrix)
    if matrix.active_mode == OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER:
        raw = route_facts.normal_raw
        cutover = route_facts.normal_cutover
    else:
        raw = route_facts.promoted_raw
        cutover = route_facts.promoted_cutover
    permit_sha256 = _permit_sha256(active_route)
    route_payload = {
        "mode": matrix.active_mode,
        "holder_site": term.holder_site,
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.writer_lease_id,
        "witness_transition_id": term.witness_transition_id,
        "stream_generation_id": active_route.source_pin.binding.stream_generation_id,
        "source_cutover_attestation_sha256": cutover.attestation_sha256,
        "receiver_permit_sha256": permit_sha256,
        "canonical_source_cutover_sha256": hashlib.sha256(raw).hexdigest(),
    }
    return ObjectDeltaRoleMatrixActivationRecord(
        active_mode=matrix.active_mode,
        holder_site=term.holder_site,
        writer_epoch=term.writer_epoch,
        writer_lease_id=term.writer_lease_id,
        witness_transition_id=term.witness_transition_id,
        stream_generation_id=active_route.source_pin.binding.stream_generation_id,
        source_cutover_attestation_sha256=cutover.attestation_sha256,
        receiver_permit_sha256=permit_sha256,
        route_artifact_sha256=hashlib.sha256(canonical_json_bytes(route_payload)).hexdigest(),
    )


def _matrix_term_route_facts(
    *,
    matrix_value: object,
    witnessed_term_value: object,
    route_generations_value: object,
    now: datetime,
    require_live_term: bool,
) -> _ActivationFacts:
    try:
        matrix = require_verified_object_delta_role_matrix(matrix_value)
    except ObjectDeltaRoleMatrixError as exc:
        raise ObjectDeltaRoleMatrixRolloverError("prior role matrix is not verified") from exc
    term = _validated_witnessed_term(
        witnessed_term_value,
        now=now,
        require_live=require_live_term,
    )
    routes, route_facts = _validated_route_generations(route_generations_value)
    if (
        matrix.normal_route != routes.normal_route
        or matrix.promoted_route != routes.promoted_route
    ):
        raise ObjectDeltaRoleMatrixRolloverError(
            "role matrix does not match verified route generations"
        )
    active_route = active_object_delta_role_matrix_route(matrix)
    if (
        matrix.active_writer_term.holder_site,
        matrix.active_writer_term.writer_epoch,
        matrix.active_writer_term.writer_lease_id,
    ) != (term.holder_site, term.writer_epoch, term.writer_lease_id):
        raise ObjectDeltaRoleMatrixRolloverError(
            "role matrix active term does not match the witnessed term"
        )
    if matrix.active_mode == OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER:
        active_cutover = route_facts.normal_cutover
    else:
        active_cutover = route_facts.promoted_cutover
    active_permit = active_route.receiver_binding.permit
    if (
        active_cutover.writer_epoch,
        active_cutover.writer_lease_id,
        active_permit.writer_epoch,
        active_permit.writer_lease_id,
    ) != (
        term.writer_epoch,
        term.writer_lease_id,
        term.writer_epoch,
        term.writer_lease_id,
    ):
        raise ObjectDeltaRoleMatrixRolloverError(
            "active source cutover and receiver permit do not match the witnessed term"
        )
    if active_route.source_pin.binding.source_site != term.holder_site:
        raise ObjectDeltaRoleMatrixRolloverError(
            "witnessed term holder does not match the active source route"
        )
    return _ActivationFacts(
        matrix=matrix,
        term=term,
        routes=routes,
        route_facts=route_facts,
        record=_record_for(matrix=matrix, term=term, route_facts=route_facts),
    )


def _validate_record(value: object) -> ObjectDeltaRoleMatrixActivationRecord:
    if type(value) is not ObjectDeltaRoleMatrixActivationRecord:
        raise ObjectDeltaRoleMatrixRolloverError("activation lineage record is invalid")
    if value.active_mode not in OBJECT_DELTA_ROLE_MATRIX_MODES:
        raise ObjectDeltaRoleMatrixRolloverError("activation lineage mode is invalid")
    if value.holder_site not in WEBAPP_SITES:
        raise ObjectDeltaRoleMatrixRolloverError("activation lineage holder site is invalid")
    if type(value.writer_epoch) is not int or value.writer_epoch < 1:
        raise ObjectDeltaRoleMatrixRolloverError("activation lineage epoch is invalid")
    if not isinstance(value.writer_lease_id, str) or LEASE_ID_RE.fullmatch(value.writer_lease_id) is None:
        raise ObjectDeltaRoleMatrixRolloverError("activation lineage lease is invalid")
    if (
        not isinstance(value.witness_transition_id, str)
        or _WITNESS_TRANSITION_ID_RE.fullmatch(value.witness_transition_id) is None
    ):
        raise ObjectDeltaRoleMatrixRolloverError("activation lineage transition is invalid")
    if not isinstance(value.stream_generation_id, str) or not value.stream_generation_id:
        raise ObjectDeltaRoleMatrixRolloverError("activation lineage route generation is invalid")
    for label, digest in (
        ("source cutover", value.source_cutover_attestation_sha256),
        ("receiver permit", value.receiver_permit_sha256),
        ("route artifact", value.route_artifact_sha256),
    ):
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ObjectDeltaRoleMatrixRolloverError(f"activation lineage {label} digest is invalid")
    expected_holder = (
        "webapp_fi"
        if value.active_mode == OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER
        else "webapp_ir"
    )
    if value.holder_site != expected_holder:
        raise ObjectDeltaRoleMatrixRolloverError("activation lineage holder does not match its role")
    return value


def _validated_history(
    value: object,
    *,
    current: ObjectDeltaRoleMatrixActivationRecord,
) -> tuple[ObjectDeltaRoleMatrixActivationRecord, ...]:
    if type(value) is not tuple or not value:
        raise ObjectDeltaRoleMatrixRolloverError("activation lineage is invalid")
    records = tuple(_validate_record(item) for item in value)
    if records[0].active_mode != OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER:
        raise ObjectDeltaRoleMatrixRolloverError("activation lineage must bootstrap from normal FI writer")
    for previous, record in zip(records, records[1:]):
        if record.writer_epoch <= previous.writer_epoch:
            raise ObjectDeltaRoleMatrixRolloverError("activation lineage term order is invalid")
        if record.active_mode == previous.active_mode:
            raise ObjectDeltaRoleMatrixRolloverError("activation lineage role transition is invalid")
    for label, values in (
        ("term", [(item.writer_epoch, item.writer_lease_id) for item in records]),
        ("Witness transition", [item.witness_transition_id for item in records]),
        ("route generation", [item.stream_generation_id for item in records]),
        ("source cutover", [item.source_cutover_attestation_sha256 for item in records]),
        ("receiver permit", [item.receiver_permit_sha256 for item in records]),
        ("route artifact", [item.route_artifact_sha256 for item in records]),
    ):
        if len(set(values)) != len(values):
            raise ObjectDeltaRoleMatrixRolloverError(f"activation lineage {label} replay is invalid")
    if records[-1] != current:
        raise ObjectDeltaRoleMatrixRolloverError(
            "activation lineage does not match its current verified matrix"
        )
    return records


def _validated_activation(
    value: object,
    *,
    now: datetime,
    require_live_term: bool,
) -> tuple[VerifiedObjectDeltaRoleMatrixActivation, _ActivationFacts]:
    if type(value) is not VerifiedObjectDeltaRoleMatrixActivation:
        raise ObjectDeltaRoleMatrixRolloverError("verified role-matrix activation capability is required")
    if value._capability is not _VERIFIED_ACTIVATION_CAPABILITY:
        raise ObjectDeltaRoleMatrixRolloverError("verified role-matrix activation was not authorized")
    facts = _matrix_term_route_facts(
        matrix_value=value._matrix,
        witnessed_term_value=value._witnessed_term,
        route_generations_value=value._route_generations,
        now=now,
        require_live_term=require_live_term,
    )
    _validated_history(value._history, current=facts.record)
    return value, facts


def _same_route_invariants(
    prior: ObjectDeltaRoleMatrixRoute,
    candidate: ObjectDeltaRoleMatrixRoute,
    *,
    label: str,
) -> None:
    prior_source = prior.source_pin.binding
    candidate_source = candidate.source_pin.binding
    if (
        prior_source.source_site,
        prior_source.destination_site,
        prior_source.campaign_id,
        prior_source.release_sha,
        prior_source.expected_registry_fingerprint,
    ) != (
        candidate_source.source_site,
        candidate_source.destination_site,
        candidate_source.campaign_id,
        candidate_source.release_sha,
        candidate_source.expected_registry_fingerprint,
    ):
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} route campaign/release/registry changed")
    if prior.source_pin.transport_policy != candidate.source_pin.transport_policy:
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} route transport policy changed")
    if prior.source_pin.expected_source_public_key != candidate.source_pin.expected_source_public_key:
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} route source key changed")
    if prior.receiver_binding.controller_public_key != candidate.receiver_binding.controller_public_key:
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} route controller key changed")
    if prior.receiver_binding.source_public_key != candidate.receiver_binding.source_public_key:
        raise ObjectDeltaRoleMatrixRolloverError(f"{label} route receiver source key changed")


def _candidate_matrix(
    *,
    routes: VerifiedObjectDeltaRoleMatrixRouteGenerations,
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    active_mode: object,
) -> VerifiedObjectDeltaRoleMatrix:
    if active_mode not in OBJECT_DELTA_ROLE_MATRIX_MODES:
        raise ObjectDeltaRoleMatrixRolloverError("next role-matrix mode is invalid")
    try:
        return authorize_object_delta_role_matrix(
            normal_route=routes.normal_route,
            promoted_route=routes.promoted_route,
            active_mode=active_mode,
            # This raw value is derived only after opaque signed Witness proof
            # validation.  It is never accepted from the caller as authority.
            active_writer_term=ObjectDeltaRoleMatrixWriterTerm(
                holder_site=term.holder_site,
                writer_epoch=term.writer_epoch,
                writer_lease_id=term.writer_lease_id,
            ),
        )
    except ObjectDeltaRoleMatrixError as exc:
        raise ObjectDeltaRoleMatrixRolloverError(
            "fresh route generations do not admit the witnessed role"
        ) from exc


def bootstrap_object_delta_role_matrix_activation(
    *,
    prior_verified_matrix: VerifiedObjectDeltaRoleMatrix,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    route_generations: VerifiedObjectDeltaRoleMatrixRouteGenerations,
    now: datetime,
) -> VerifiedObjectDeltaRoleMatrixActivation:
    """Bootstrap the initial normal-FI activation from verified evidence only.

    Promotion is intentionally not a bootstrap mode: it must have a verified
    normal predecessor and a strictly newer witnessed term.
    """

    facts = _matrix_term_route_facts(
        matrix_value=prior_verified_matrix,
        witnessed_term_value=witnessed_term,
        route_generations_value=route_generations,
        now=now,
        require_live_term=True,
    )
    if facts.matrix.active_mode != OBJECT_DELTA_ROLE_MATRIX_MODE_NORMAL_FI_WRITER:
        raise ObjectDeltaRoleMatrixRolloverError(
            "only normal FI writer may bootstrap a role-matrix activation"
        )
    result = VerifiedObjectDeltaRoleMatrixActivation(
        _matrix=facts.matrix,
        _witnessed_term=facts.term,
        _route_generations=facts.routes,
        _history=(facts.record,),
    )
    object.__setattr__(result, "_capability", _VERIFIED_ACTIVATION_CAPABILITY)
    _validated_activation(result, now=now, require_live_term=True)
    return result


def rollover_object_delta_role_matrix_activation(
    *,
    prior_activation: VerifiedObjectDeltaRoleMatrixActivation,
    witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    fresh_route_generations: VerifiedObjectDeltaRoleMatrixRouteGenerations,
    next_active_mode: str,
    now: datetime,
) -> VerifiedObjectDeltaRoleMatrixActivation:
    """Admit exactly one strict normal→IR or IR→normal role transition.

    The fresh term and the newly active direction's source cutover/permit must
    all agree.  The counterpart direction is required to be exactly unchanged
    from the prior activation, preventing stale artifacts from being swapped
    in while it is inactive.
    """

    _prior, prior = _validated_activation(
        prior_activation,
        now=now,
        # A former term is normally expired when the other site may acquire
        # its successor, so it remains authenticated but need not be live.
        require_live_term=False,
    )
    fresh_term = _validated_witnessed_term(witnessed_term, now=now, require_live=True)
    fresh_routes, fresh_route_facts = _validated_route_generations(fresh_route_generations)
    matrix = _candidate_matrix(
        routes=fresh_routes,
        term=fresh_term,
        active_mode=next_active_mode,
    )
    candidate = _matrix_term_route_facts(
        matrix_value=matrix,
        witnessed_term_value=fresh_term,
        route_generations_value=fresh_routes,
        now=now,
        require_live_term=True,
    )
    # The second validation above has independently rebuilt the facts.  Keep
    # the first opaque revalidation to make direct/replaced input failures
    # occur before any derived matrix is considered.
    if candidate.route_facts != fresh_route_facts:
        raise ObjectDeltaRoleMatrixRolloverError("fresh route generations changed during validation")
    if candidate.matrix.active_mode == prior.matrix.active_mode:
        raise ObjectDeltaRoleMatrixRolloverError("same-role activation replay is forbidden")

    _same_route_invariants(prior.routes.normal_route, candidate.routes.normal_route, label="normal")
    _same_route_invariants(
        prior.routes.promoted_route,
        candidate.routes.promoted_route,
        label="promoted",
    )
    if candidate.matrix.active_mode == OBJECT_DELTA_ROLE_MATRIX_MODE_PROMOTED_IR_WRITER:
        if (
            candidate.routes.normal_route != prior.routes.normal_route
            or candidate.route_facts.normal_raw != prior.route_facts.normal_raw
        ):
            raise ObjectDeltaRoleMatrixRolloverError(
                "promotion may not replace the inactive normal route artifact"
            )
        if (
            candidate.routes.promoted_route == prior.routes.promoted_route
            or candidate.route_facts.promoted_raw == prior.route_facts.promoted_raw
        ):
            raise ObjectDeltaRoleMatrixRolloverError(
                "promotion requires a fresh promoted route generation artifact"
            )
    else:
        if (
            candidate.routes.promoted_route != prior.routes.promoted_route
            or candidate.route_facts.promoted_raw != prior.route_facts.promoted_raw
        ):
            raise ObjectDeltaRoleMatrixRolloverError(
                "failback may not replace the inactive promoted route artifact"
            )
        if (
            candidate.routes.normal_route == prior.routes.normal_route
            or candidate.route_facts.normal_raw == prior.route_facts.normal_raw
        ):
            raise ObjectDeltaRoleMatrixRolloverError(
                "failback requires a fresh normal route generation artifact"
            )

    prior_history = prior_activation._history
    candidate_record = candidate.record
    if candidate_record.writer_epoch <= max(record.writer_epoch for record in prior_history):
        raise ObjectDeltaRoleMatrixRolloverError("Witness term regression or replay is forbidden")
    if candidate_record.writer_lease_id in {record.writer_lease_id for record in prior_history}:
        raise ObjectDeltaRoleMatrixRolloverError("Witness lease replay is forbidden")
    if candidate_record.witness_transition_id in {
        record.witness_transition_id for record in prior_history
    }:
        raise ObjectDeltaRoleMatrixRolloverError("Witness transition replay is forbidden")
    if candidate_record.stream_generation_id in {
        record.stream_generation_id for record in prior_history
    }:
        raise ObjectDeltaRoleMatrixRolloverError("active route generation replay is forbidden")
    if candidate_record.source_cutover_attestation_sha256 in {
        record.source_cutover_attestation_sha256 for record in prior_history
    }:
        raise ObjectDeltaRoleMatrixRolloverError("source cutover artifact replay is forbidden")
    if candidate_record.receiver_permit_sha256 in {
        record.receiver_permit_sha256 for record in prior_history
    }:
        raise ObjectDeltaRoleMatrixRolloverError("receiver permit replay is forbidden")
    if candidate_record.route_artifact_sha256 in {
        record.route_artifact_sha256 for record in prior_history
    }:
        raise ObjectDeltaRoleMatrixRolloverError("route artifact replay is forbidden")

    result = VerifiedObjectDeltaRoleMatrixActivation(
        _matrix=candidate.matrix,
        _witnessed_term=candidate.term,
        _route_generations=candidate.routes,
        _history=prior_history + (candidate.record,),
    )
    object.__setattr__(result, "_capability", _VERIFIED_ACTIVATION_CAPABILITY)
    _validated_activation(result, now=now, require_live_term=True)
    return result


def require_verified_object_delta_role_matrix_activation(
    value: object,
    *,
    now: datetime,
) -> VerifiedObjectDeltaRoleMatrixActivation:
    """Revalidate archived activation lineage, including expired predecessor terms."""

    activation, _facts = _validated_activation(value, now=now, require_live_term=False)
    return activation


def require_live_object_delta_role_matrix_activation(
    value: object,
    *,
    now: datetime,
) -> VerifiedObjectDeltaRoleMatrixActivation:
    """Require a currently live activation before a future route adapter uses it."""

    activation, _facts = _validated_activation(value, now=now, require_live_term=True)
    return activation


def project_active_object_delta_role_matrix_role(
    value: VerifiedObjectDeltaRoleMatrixActivation,
    *,
    site: str,
    now: datetime,
):
    """Project only one site's current active role from a live activation.

    No public helper projects an inactive route or turns this value into a
    writer start.  A future adapter must use this projection alongside its
    own fresh live-Witness and durable-lineage checks.
    """

    _activation, facts = _validated_activation(value, now=now, require_live_term=True)
    try:
        return object_delta_role_matrix_site_role(facts.matrix, site=site)
    except ObjectDeltaRoleMatrixError as exc:
        raise ObjectDeltaRoleMatrixRolloverError("requested active role is invalid") from exc
