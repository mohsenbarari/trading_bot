"""Pure fail-closed admission contract for physical PostgreSQL/WAL promotion.

The Object-delta MVP deliberately cannot certify the required full FI/IR
mirror.  This module describes the *next* data plane instead: a physical
PostgreSQL baseline, an ordered WAL frontier, and an independently checked
object/blob frontier.  It is intentionally an admission check only.  It
does not open files, contact a Witness, SSH to a host, call PostgreSQL,
read Object Storage, change a route, promote a standby, or write state.

Raw receipts are not authority.  They first need Ed25519 verification through
``verify_physical_wal_promotion_evidence``; that function mints an opaque
capability.  The gate then re-verifies that capability against an opaque,
signed Writer-Witness term and a verified prior role-matrix activation.  It
requires a source acknowledgement mode which represents a strict remote
durable/replay contract *and* an independently verified pull-plane remote-ack
request/receipt pair.  A generic signed claim without that second evidence
cannot make the strict path eligible.

Consequently an ``eligible`` result is never a writer-start capability.  A
future root-only coordinator must still perform a live Witness re-check,
durably CAS/consume the term and continuity lineage, fence the former writer,
atomically install the promotion, and enforce the transport's strict
durability/replay semantics.  This pure gate cannot implement any of those
cross-process guarantees.
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

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    canonical_json_bytes,
)
from core.object_delta_role_matrix import (
    OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER,
    OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE,
    ObjectDeltaRoleMatrixError,
    active_object_delta_role_matrix_route,
    object_delta_role_matrix_site_role,
    require_verified_object_delta_role_matrix,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixActivation,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    require_live_object_delta_role_matrix_witnessed_term,
    require_verified_object_delta_role_matrix_activation,
    require_verified_object_delta_role_matrix_witnessed_term,
)
from core.physical_wal_remote_ack import (
    PhysicalWalRemoteAckError,
    require_verified_physical_wal_remote_ack_evidence,
)


PHYSICAL_WAL_PROMOTION_GATE_SCHEMA = "gold-trade-physical-wal-promotion-gate-v1"
PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA = (
    "gold-trade-physical-wal-source-durability-receipt-v1"
)
PHYSICAL_WAL_RECEIVER_REPLAY_RECEIPT_SCHEMA = (
    "gold-trade-physical-wal-receiver-replay-receipt-v1"
)
PHYSICAL_WAL_BLOB_OBJECT_RECEIPT_SCHEMA = (
    "gold-trade-physical-wal-blob-object-receipt-v1"
)
PHYSICAL_WAL_CONTINUITY_ARTIFACT_SCHEMA = (
    "gold-trade-physical-wal-continuity-artifact-v1"
)

PHYSICAL_WAL_ACKNOWLEDGEMENT_MODE_STRICT_REMOTE_DURABLE_REPLAY = (
    "strict_remote_durable_replay"
)
# Archive completion is valuable recovery evidence, but it is explicitly not
# an acknowledgement frontier for a no-loss writer transition.
PHYSICAL_WAL_ACKNOWLEDGEMENT_MODE_ARCHIVE_ONLY = "archive_only"
MAX_PHYSICAL_WAL_EVIDENCE_AGE_SECONDS = 60
MAX_PHYSICAL_WAL_EVIDENCE_FUTURE_SKEW_SECONDS = 5
MAX_PHYSICAL_WAL_EVIDENCE_BYTES = 32 * 1024

_VERIFIED_PHYSICAL_WAL_PROMOTION_EVIDENCE_CAPABILITY = object()
_PHYSICAL_WAL_PROMOTION_ASSESSMENT_CAPABILITY = object()
_CONTINUITY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_LSN_RE = re.compile(r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$")
_REGISTRY_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")


class PhysicalWalPromotionGateError(ValueError):
    """Evidence or a requested promotion does not meet this local contract."""


@dataclass(frozen=True)
class VerifiedPhysicalWalPromotionEvidence:
    """Opaque, signature-verified receipts for one proposed continuity point.

    Direct construction and ``dataclasses.replace`` do not mint authority;
    every later use re-parses and re-verifies all four signed artifacts.
    """

    source_durability_receipt: bytes
    receiver_replay_receipt: bytes
    blob_object_receipt: bytes
    continuity_artifact: bytes
    source_public_key: bytes
    controller_public_key: bytes
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalWalPromotionAssessment:
    """A non-authorizing local result; only ``eligible`` may proceed to CAS."""

    status: str
    reason_codes: tuple[str, ...]
    source_site: str | None = None
    target_site: str | None = None
    baseline_generation_id: str | None = None
    acknowledged_durable_wal_lsn: str | None = None
    receiver_replay_wal_lsn: str | None = None
    blob_object_frontier_wal_lsn: str | None = None
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def eligible(self) -> bool:
        return (
            self.status == "eligible"
            and self._capability is _PHYSICAL_WAL_PROMOTION_ASSESSMENT_CAPABILITY
        )


@dataclass(frozen=True)
class _SignedArtifact:
    payload: dict[str, Any]
    raw: bytes
    sha256: str


@dataclass(frozen=True)
class _EvidenceFacts:
    source: _SignedArtifact
    receiver: _SignedArtifact
    blob: _SignedArtifact
    continuity: _SignedArtifact


@dataclass(frozen=True)
class _PriorActivationContext:
    source_site: str
    target_site: str
    campaign_id: str
    release_sha: str
    registry_fingerprint: str
    stream_generation_id: str
    destination_age_recipient: str
    source_public_key: bytes
    controller_public_key: bytes
    source_key_sha256: str
    controller_key_sha256: str
    transport_policy_sha256: str
    route_binding_sha256: str
    prior_term_proof_sha256: str
    prior_holder_site: str
    prior_writer_epoch: int
    prior_writer_lease_id: str
    historical_writer_lease_ids: frozenset[str]
    historical_witness_transition_ids: frozenset[str]


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PhysicalWalPromotionGateError("signed artifact contains duplicate JSON fields")
        result[key] = value
    return result


def _utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PhysicalWalPromotionGateError(f"{label} is invalid")
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PhysicalWalPromotionGateError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PhysicalWalPromotionGateError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise PhysicalWalPromotionGateError(f"{label} is invalid")
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        raise PhysicalWalPromotionGateError(f"{label} is not canonical UTC")
    return normalized


def _require_hash(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise PhysicalWalPromotionGateError(f"{label} is invalid")
    return value


def _require_id(value: object, *, label: str, pattern: re.Pattern[str] = _CONTINUITY_ID_RE) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PhysicalWalPromotionGateError(f"{label} is invalid")
    return value


def _require_site(value: object, *, label: str) -> str:
    if value not in {"webapp_fi", "webapp_ir"}:
        raise PhysicalWalPromotionGateError(f"{label} is invalid")
    return value


def _parse_lsn(value: object, *, label: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        raise PhysicalWalPromotionGateError(f"{label} is invalid")
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _validate_public_key(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        raise PhysicalWalPromotionGateError(f"{label} is invalid")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(value)
    except (ImportError, ValueError) as exc:
        raise PhysicalWalPromotionGateError(f"{label} is invalid") from exc
    return value


def _decode_signature(value: object) -> bytes:
    if not isinstance(value, str):
        raise PhysicalWalPromotionGateError("signed artifact signature is invalid")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise PhysicalWalPromotionGateError("signed artifact signature is invalid") from exc
    if len(decoded) != 64:
        raise PhysicalWalPromotionGateError("signed artifact signature is invalid")
    return decoded


def _parse_signed_artifact(value: object, *, label: str, public_key: bytes) -> _SignedArtifact:
    if isinstance(value, Mapping):
        try:
            raw = canonical_json_bytes(dict(value))
            payload = dict(value)
        except (TypeError, ValueError) as exc:
            raise PhysicalWalPromotionGateError(f"{label} is not canonical JSON") from exc
    elif isinstance(value, bytes):
        if not value or len(value) > MAX_PHYSICAL_WAL_EVIDENCE_BYTES:
            raise PhysicalWalPromotionGateError(f"{label} byte size is invalid")
        raw = value
        try:
            payload = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_strict_object)
        except PhysicalWalPromotionGateError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PhysicalWalPromotionGateError(f"{label} is invalid JSON") from exc
        if not isinstance(payload, dict) or canonical_json_bytes(payload) != raw:
            raise PhysicalWalPromotionGateError(f"{label} is not canonical JSON")
    else:
        raise PhysicalWalPromotionGateError(f"{label} is invalid")
    if len(raw) > MAX_PHYSICAL_WAL_EVIDENCE_BYTES:
        raise PhysicalWalPromotionGateError(f"{label} byte size is invalid")
    signature = _decode_signature(payload.get("signature"))
    unsigned = {key: item for key, item in payload.items() if key != "signature"}
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, canonical_json_bytes(unsigned))
    except (InvalidSignature, ValueError) as exc:
        raise PhysicalWalPromotionGateError(f"{label} signature is invalid") from exc
    return _SignedArtifact(payload=payload, raw=raw, sha256=hashlib.sha256(raw).hexdigest())


_COMMON_FIELDS = frozenset(
    {
        "continuity_id",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "registry_fingerprint",
        "stream_generation_id",
        "baseline_generation_id",
        "baseline_manifest_sha256",
        "source_key_sha256",
        "controller_key_sha256",
        "transport_policy_sha256",
        "route_binding_sha256",
        "prior_term_proof_sha256",
        "prior_holder_site",
        "prior_writer_epoch",
        "prior_writer_lease_id",
    }
)

_SOURCE_FIELDS = _COMMON_FIELDS | frozenset(
    {
        "schema",
        "kind",
        "acknowledgement_mode",
        "baseline_wal_lsn",
        "acknowledged_durable_wal_lsn",
        "observed_at",
        "signature",
    }
)
_RECEIVER_FIELDS = _COMMON_FIELDS | frozenset(
    {
        "schema",
        "kind",
        "source_durability_receipt_sha256",
        "receiver_replay_wal_lsn",
        "observed_at",
        "signature",
    }
)
_BLOB_FIELDS = _COMMON_FIELDS | frozenset(
    {
        "schema",
        "kind",
        "source_durability_receipt_sha256",
        "receiver_replay_receipt_sha256",
        "blob_object_frontier_wal_lsn",
        "objects_complete",
        "object_manifest_sha256",
        "object_manifest_version_id",
        "observed_at",
        "signature",
    }
)
_CONTINUITY_FIELDS = _COMMON_FIELDS | frozenset(
    {
        "schema",
        "kind",
        "candidate_term_proof_sha256",
        "candidate_holder_site",
        "candidate_writer_epoch",
        "candidate_writer_lease_id",
        "source_durability_receipt_sha256",
        "receiver_replay_receipt_sha256",
        "blob_object_receipt_sha256",
        "source_acknowledged_durable_wal_lsn",
        "receiver_replay_wal_lsn",
        "blob_object_frontier_wal_lsn",
        "objects_complete",
        # On the strict path these are the exact SHA-256 identities of the
        # signed source request and destination receipt supplied separately
        # to this gate.  They are null only for the explicitly non-strict
        # archive-only path.
        "remote_ack_request_sha256",
        "remote_ack_receipt_sha256",
        "issued_at",
        "signature",
    }
)


def _validate_common(payload: Mapping[str, Any], *, label: str) -> None:
    _require_id(payload.get("continuity_id"), label=f"{label} continuity ID")
    source = _require_site(payload.get("source_site"), label=f"{label} source site")
    destination = _require_site(payload.get("destination_site"), label=f"{label} destination site")
    if source == destination:
        raise PhysicalWalPromotionGateError(f"{label} source and destination overlap")
    _require_id(payload.get("campaign_id"), label=f"{label} campaign", pattern=CAMPAIGN_ID_RE)
    release = payload.get("release_sha")
    if not isinstance(release, str) or RELEASE_SHA_RE.fullmatch(release) is None:
        raise PhysicalWalPromotionGateError(f"{label} release is invalid")
    fingerprint = payload.get("registry_fingerprint")
    if not isinstance(fingerprint, str) or _REGISTRY_FINGERPRINT_RE.fullmatch(fingerprint) is None:
        raise PhysicalWalPromotionGateError(f"{label} registry fingerprint is invalid")
    _require_id(
        payload.get("stream_generation_id"),
        label=f"{label} stream generation",
        pattern=STREAM_GENERATION_ID_RE,
    )
    _require_id(
        payload.get("baseline_generation_id"),
        label=f"{label} baseline generation",
        pattern=STREAM_GENERATION_ID_RE,
    )
    for field_name in (
        "baseline_manifest_sha256",
        "source_key_sha256",
        "controller_key_sha256",
        "transport_policy_sha256",
        "route_binding_sha256",
        "prior_term_proof_sha256",
    ):
        _require_hash(payload.get(field_name), label=f"{label} {field_name}")
    _require_site(payload.get("prior_holder_site"), label=f"{label} prior term holder")
    epoch = payload.get("prior_writer_epoch")
    if type(epoch) is not int or epoch < 1:
        raise PhysicalWalPromotionGateError(f"{label} prior term epoch is invalid")
    lease = payload.get("prior_writer_lease_id")
    if not isinstance(lease, str) or LEASE_ID_RE.fullmatch(lease) is None:
        raise PhysicalWalPromotionGateError(f"{label} prior term lease is invalid")


def _validate_artifact_shape(artifact: _SignedArtifact, *, kind: str) -> None:
    payload = artifact.payload
    if kind == "source":
        expected_fields = _SOURCE_FIELDS
        expected_schema = PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA
        expected_kind = "source_durable_wal_frontier"
    elif kind == "receiver":
        expected_fields = _RECEIVER_FIELDS
        expected_schema = PHYSICAL_WAL_RECEIVER_REPLAY_RECEIPT_SCHEMA
        expected_kind = "receiver_replay_wal_frontier"
    elif kind == "blob":
        expected_fields = _BLOB_FIELDS
        expected_schema = PHYSICAL_WAL_BLOB_OBJECT_RECEIPT_SCHEMA
        expected_kind = "blob_object_frontier"
    elif kind == "continuity":
        expected_fields = _CONTINUITY_FIELDS
        expected_schema = PHYSICAL_WAL_CONTINUITY_ARTIFACT_SCHEMA
        expected_kind = "physical_wal_continuity"
    else:  # pragma: no cover - internal call-site invariant.
        raise AssertionError(kind)
    if set(payload) != expected_fields or payload.get("schema") != expected_schema:
        raise PhysicalWalPromotionGateError(f"{kind} artifact schema is invalid")
    if payload.get("kind") != expected_kind:
        raise PhysicalWalPromotionGateError(f"{kind} artifact kind is invalid")
    _validate_common(payload, label=kind)
    if kind == "source":
        if payload.get("acknowledgement_mode") not in {
            PHYSICAL_WAL_ACKNOWLEDGEMENT_MODE_STRICT_REMOTE_DURABLE_REPLAY,
            PHYSICAL_WAL_ACKNOWLEDGEMENT_MODE_ARCHIVE_ONLY,
        }:
            raise PhysicalWalPromotionGateError("source acknowledgement mode is invalid")
        _parse_lsn(payload.get("baseline_wal_lsn"), label="source baseline WAL LSN")
        _parse_lsn(
            payload.get("acknowledged_durable_wal_lsn"),
            label="source acknowledged durable WAL LSN",
        )
        _timestamp(payload.get("observed_at"), label="source observed_at")
    elif kind == "receiver":
        _require_hash(
            payload.get("source_durability_receipt_sha256"),
            label="receiver source receipt hash",
        )
        _parse_lsn(payload.get("receiver_replay_wal_lsn"), label="receiver replay WAL LSN")
        _timestamp(payload.get("observed_at"), label="receiver observed_at")
    elif kind == "blob":
        _require_hash(
            payload.get("source_durability_receipt_sha256"),
            label="blob source receipt hash",
        )
        _require_hash(
            payload.get("receiver_replay_receipt_sha256"),
            label="blob receiver receipt hash",
        )
        _parse_lsn(
            payload.get("blob_object_frontier_wal_lsn"),
            label="blob object frontier WAL LSN",
        )
        if type(payload.get("objects_complete")) is not bool:
            raise PhysicalWalPromotionGateError("blob objects_complete is invalid")
        _require_hash(payload.get("object_manifest_sha256"), label="blob object manifest hash")
        _require_id(
            payload.get("object_manifest_version_id"),
            label="blob object manifest version",
            pattern=VERSION_ID_RE,
        )
        _timestamp(payload.get("observed_at"), label="blob observed_at")
    else:
        for field_name in (
            "candidate_term_proof_sha256",
            "source_durability_receipt_sha256",
            "receiver_replay_receipt_sha256",
            "blob_object_receipt_sha256",
        ):
            _require_hash(payload.get(field_name), label=f"continuity {field_name}")
        _require_site(payload.get("candidate_holder_site"), label="continuity candidate holder")
        epoch = payload.get("candidate_writer_epoch")
        if type(epoch) is not int or epoch < 1:
            raise PhysicalWalPromotionGateError("continuity candidate epoch is invalid")
        lease = payload.get("candidate_writer_lease_id")
        if not isinstance(lease, str) or LEASE_ID_RE.fullmatch(lease) is None:
            raise PhysicalWalPromotionGateError("continuity candidate lease is invalid")
        for field_name in (
            "source_acknowledged_durable_wal_lsn",
            "receiver_replay_wal_lsn",
            "blob_object_frontier_wal_lsn",
        ):
            _parse_lsn(payload.get(field_name), label=f"continuity {field_name}")
        if type(payload.get("objects_complete")) is not bool:
            raise PhysicalWalPromotionGateError("continuity objects_complete is invalid")
        for field_name in ("remote_ack_request_sha256", "remote_ack_receipt_sha256"):
            value = payload.get(field_name)
            if value is None:
                continue
            digest = _require_hash(value, label=f"continuity {field_name}")
            if digest == "0" * 64:
                raise PhysicalWalPromotionGateError(
                    f"continuity {field_name} is invalid"
                )
        _timestamp(payload.get("issued_at"), label="continuity issued_at")


def _evidence_facts(
    value: object,
    *,
    source_public_key: bytes | None = None,
    controller_public_key: bytes | None = None,
) -> _EvidenceFacts:
    if type(value) is VerifiedPhysicalWalPromotionEvidence:
        if value._capability is not _VERIFIED_PHYSICAL_WAL_PROMOTION_EVIDENCE_CAPABILITY:
            raise PhysicalWalPromotionGateError("verified physical WAL evidence was not authorized")
        source_key = _validate_public_key(value.source_public_key, label="verified source public key")
        controller_key = _validate_public_key(
            value.controller_public_key,
            label="verified controller public key",
        )
        if source_public_key is not None and source_key != source_public_key:
            raise PhysicalWalPromotionGateError("verified source key does not match active route")
        if controller_public_key is not None and controller_key != controller_public_key:
            raise PhysicalWalPromotionGateError("verified controller key does not match active route")
        source = _parse_signed_artifact(
            value.source_durability_receipt,
            label="source durability receipt",
            public_key=source_key,
        )
        receiver = _parse_signed_artifact(
            value.receiver_replay_receipt,
            label="receiver replay receipt",
            public_key=controller_key,
        )
        blob = _parse_signed_artifact(
            value.blob_object_receipt,
            label="blob object receipt",
            public_key=controller_key,
        )
        continuity = _parse_signed_artifact(
            value.continuity_artifact,
            label="continuity artifact",
            public_key=controller_key,
        )
    else:
        raise PhysicalWalPromotionGateError("verified physical WAL evidence capability is required")
    for artifact, kind in (
        (source, "source"),
        (receiver, "receiver"),
        (blob, "blob"),
        (continuity, "continuity"),
    ):
        _validate_artifact_shape(artifact, kind=kind)
    return _EvidenceFacts(source=source, receiver=receiver, blob=blob, continuity=continuity)


def verify_physical_wal_promotion_evidence(
    *,
    source_durability_receipt: Mapping[str, Any] | bytes,
    receiver_replay_receipt: Mapping[str, Any] | bytes,
    blob_object_receipt: Mapping[str, Any] | bytes,
    continuity_artifact: Mapping[str, Any] | bytes,
    source_public_key: bytes,
    controller_public_key: bytes,
) -> VerifiedPhysicalWalPromotionEvidence:
    """Verify four signed raw artifacts and mint a non-authorizing capability.

    This verifies syntax, canonical bytes, and signatures.  It deliberately
    does not make a continuity bundle eligible: only the assessment below can
    compare it with the opaque prior activation and fresh Witness term.
    """

    source_key = _validate_public_key(source_public_key, label="source public key")
    controller_key = _validate_public_key(controller_public_key, label="controller public key")
    source = _parse_signed_artifact(
        source_durability_receipt,
        label="source durability receipt",
        public_key=source_key,
    )
    receiver = _parse_signed_artifact(
        receiver_replay_receipt,
        label="receiver replay receipt",
        public_key=controller_key,
    )
    blob = _parse_signed_artifact(
        blob_object_receipt,
        label="blob object receipt",
        public_key=controller_key,
    )
    continuity = _parse_signed_artifact(
        continuity_artifact,
        label="continuity artifact",
        public_key=controller_key,
    )
    for artifact, kind in (
        (source, "source"),
        (receiver, "receiver"),
        (blob, "blob"),
        (continuity, "continuity"),
    ):
        _validate_artifact_shape(artifact, kind=kind)
    result = VerifiedPhysicalWalPromotionEvidence(
        source_durability_receipt=source.raw,
        receiver_replay_receipt=receiver.raw,
        blob_object_receipt=blob.raw,
        continuity_artifact=continuity.raw,
        source_public_key=source_key,
        controller_public_key=controller_key,
    )
    object.__setattr__(result, "_capability", _VERIFIED_PHYSICAL_WAL_PROMOTION_EVIDENCE_CAPABILITY)
    _evidence_facts(result)
    return result


def require_verified_physical_wal_promotion_evidence(
    value: object,
) -> VerifiedPhysicalWalPromotionEvidence:
    """Re-verify the opaque evidence before it is assessed or handed off."""

    _evidence_facts(value)
    return value


def _active_context(
    prior_activation: object,
    *,
    now: datetime,
) -> _PriorActivationContext:
    try:
        activation = require_verified_object_delta_role_matrix_activation(prior_activation, now=now)
        matrix = require_verified_object_delta_role_matrix(activation._matrix)
        prior_term = require_verified_object_delta_role_matrix_witnessed_term(
            activation._witnessed_term,
            now=now,
        )
        active_route = active_object_delta_role_matrix_route(matrix)
        source_binding = active_route.source_pin.binding
        source_role = object_delta_role_matrix_site_role(matrix, site=source_binding.source_site)
        target_role = object_delta_role_matrix_site_role(
            matrix,
            site=source_binding.destination_site,
        )
    except (AttributeError, ObjectDeltaRoleMatrixError, ObjectDeltaRoleMatrixRolloverError) as exc:
        raise PhysicalWalPromotionGateError("prior role-matrix activation is not verified") from exc
    if (
        source_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE
        or target_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER
    ):
        raise PhysicalWalPromotionGateError("prior role-matrix active/inactive roles are invalid")
    policy = active_route.source_pin.transport_policy
    policy_payload = {
        "bucket": policy.bucket,
        "prefix": policy.prefix,
        "webapp_fi_age_recipient": policy.webapp_fi_age_recipient,
        "webapp_ir_age_recipient": policy.webapp_ir_age_recipient,
    }
    source_key = active_route.source_pin.expected_source_public_key
    controller_key = active_route.receiver_binding.controller_public_key
    destination_recipient = (
        policy.webapp_fi_age_recipient
        if source_binding.destination_site == "webapp_fi"
        else policy.webapp_ir_age_recipient
    )
    source_key_sha = hashlib.sha256(source_key).hexdigest()
    controller_key_sha = hashlib.sha256(controller_key).hexdigest()
    policy_sha = hashlib.sha256(canonical_json_bytes(policy_payload)).hexdigest()
    route_payload = {
        "source_site": source_binding.source_site,
        "destination_site": source_binding.destination_site,
        "campaign_id": source_binding.campaign_id,
        "release_sha": source_binding.release_sha,
        "registry_fingerprint": source_binding.expected_registry_fingerprint,
        "stream_generation_id": source_binding.stream_generation_id,
        "source_key_sha256": source_key_sha,
        "controller_key_sha256": controller_key_sha,
        "transport_policy_sha256": policy_sha,
    }
    return _PriorActivationContext(
        source_site=source_binding.source_site,
        target_site=source_binding.destination_site,
        campaign_id=source_binding.campaign_id,
        release_sha=source_binding.release_sha,
        registry_fingerprint=source_binding.expected_registry_fingerprint,
        stream_generation_id=source_binding.stream_generation_id,
        destination_age_recipient=destination_recipient,
        source_public_key=source_key,
        controller_public_key=controller_key,
        source_key_sha256=source_key_sha,
        controller_key_sha256=controller_key_sha,
        transport_policy_sha256=policy_sha,
        route_binding_sha256=hashlib.sha256(canonical_json_bytes(route_payload)).hexdigest(),
        prior_term_proof_sha256=prior_term.proof_sha256,
        prior_holder_site=prior_term.holder_site,
        prior_writer_epoch=prior_term.writer_epoch,
        prior_writer_lease_id=prior_term.writer_lease_id,
        historical_writer_lease_ids=frozenset(
            record.writer_lease_id for record in activation._history
        ),
        historical_witness_transition_ids=frozenset(
            record.witness_transition_id for record in activation._history
        ),
    )


def _blocked(*codes: str, context: _PriorActivationContext | None = None, facts: _EvidenceFacts | None = None) -> PhysicalWalPromotionAssessment:
    source = context.source_site if context else None
    target = context.target_site if context else None
    baseline = None
    acknowledged = None
    receiver = None
    blob = None
    if facts is not None:
        source_payload = facts.source.payload
        receiver_payload = facts.receiver.payload
        blob_payload = facts.blob.payload
        baseline = source_payload.get("baseline_generation_id")
        acknowledged = source_payload.get("acknowledged_durable_wal_lsn")
        receiver = receiver_payload.get("receiver_replay_wal_lsn")
        blob = blob_payload.get("blob_object_frontier_wal_lsn")
    return _mint_assessment(
        status="blocked",
        reason_codes=tuple(dict.fromkeys(codes)),
        source_site=source,
        target_site=target,
        baseline_generation_id=baseline,
        acknowledged_durable_wal_lsn=acknowledged,
        receiver_replay_wal_lsn=receiver,
        blob_object_frontier_wal_lsn=blob,
    )


def _mint_assessment(**kwargs: object) -> PhysicalWalPromotionAssessment:
    result = PhysicalWalPromotionAssessment(**kwargs)
    object.__setattr__(result, "_capability", _PHYSICAL_WAL_PROMOTION_ASSESSMENT_CAPABILITY)
    return result


def _all_equal(values: tuple[object, ...]) -> bool:
    return len(set(values)) == 1


def _artifact_common_values(facts: _EvidenceFacts, field_name: str) -> tuple[object, ...]:
    return tuple(
        artifact.payload[field_name]
        for artifact in (facts.source, facts.receiver, facts.blob, facts.continuity)
    )


def _evidence_fresh(facts: _EvidenceFacts, *, now: datetime) -> bool:
    observed = (
        _timestamp(facts.source.payload["observed_at"], label="source observed_at"),
        _timestamp(facts.receiver.payload["observed_at"], label="receiver observed_at"),
        _timestamp(facts.blob.payload["observed_at"], label="blob observed_at"),
        _timestamp(facts.continuity.payload["issued_at"], label="continuity issued_at"),
    )
    newest_allowed = now + timedelta(seconds=MAX_PHYSICAL_WAL_EVIDENCE_FUTURE_SKEW_SECONDS)
    oldest_allowed = now - timedelta(seconds=MAX_PHYSICAL_WAL_EVIDENCE_AGE_SECONDS)
    if any(value > newest_allowed or value < oldest_allowed for value in observed):
        return False
    return (
        observed[0] <= observed[1] <= observed[2] <= observed[3]
    )


def _remote_ack_reasons(
    value: object,
    *,
    context: _PriorActivationContext,
    facts: _EvidenceFacts,
    now: datetime,
) -> tuple[str, ...]:
    """Bind strict promotion evidence to the signed pull-plane acknowledgement.

    This remains a pure comparison.  The remote-ack contract itself validates
    an exact manifest/Object-version set, signatures, freshness, and replay
    inputs; this gate binds that opaque result to the active route and to the
    generic source/receiver/blob frontiers it is about to assess.  It does not
    persist the replay ledger or turn the acknowledgement into a DB commit.
    """

    try:
        remote = require_verified_physical_wal_remote_ack_evidence(value, now=now)
    except PhysicalWalRemoteAckError:
        return ("REMOTE_ACK_UNVERIFIED",)

    binding = remote.binding
    source = facts.source.payload
    receiver = facts.receiver.payload
    blob = facts.blob.payload
    continuity = facts.continuity.payload
    reasons: list[str] = []
    if (
        binding.source_site != context.source_site
        or binding.destination_site != context.target_site
        or binding.destination_age_recipient != context.destination_age_recipient
        or binding.campaign_id != context.campaign_id
        or binding.release_sha != context.release_sha
        or binding.stream_generation_id != context.stream_generation_id
        or binding.baseline_generation_id != source["baseline_generation_id"]
        or binding.baseline_manifest_sha256 != source["baseline_manifest_sha256"]
    ):
        reasons.append("REMOTE_ACK_ACTIVE_ROUTE_OR_BASELINE_MISMATCH")
    if (
        remote.source_public_key != context.source_public_key
        or remote.destination_public_key != context.controller_public_key
    ):
        reasons.append("REMOTE_ACK_ROUTE_KEY_MISMATCH")
    term = binding.writer_term
    if (
        term.writer_holder_site != context.prior_holder_site
        or term.writer_epoch != context.prior_writer_epoch
        or term.writer_lease_id != context.prior_writer_lease_id
        or term.witnessed_term_proof_sha256 != context.prior_term_proof_sha256
    ):
        reasons.append("REMOTE_ACK_PRIOR_TERM_MISMATCH")
    if (
        binding.target_acknowledged_wal_lsn != source["acknowledged_durable_wal_lsn"]
        or binding.blob_object_frontier_wal_lsn != blob["blob_object_frontier_wal_lsn"]
        or binding.objects_complete is not True
        or blob["objects_complete"] is not True
    ):
        reasons.append("REMOTE_ACK_FRONTIER_OR_BLOB_MISMATCH")
    request_sha256 = hashlib.sha256(remote.source_request).hexdigest()
    receipt_sha256 = hashlib.sha256(remote.destination_receipt).hexdigest()
    if (
        continuity.get("remote_ack_request_sha256") != request_sha256
        or continuity.get("remote_ack_receipt_sha256") != receipt_sha256
    ):
        reasons.append("REMOTE_ACK_CONTINUITY_BINDING_MISMATCH")
    try:
        _target, target_value = _parse_lsn(
            binding.target_acknowledged_wal_lsn,
            label="remote acknowledgement target frontier",
        )
        _receiver, receiver_value = _parse_lsn(
            receiver["receiver_replay_wal_lsn"],
            label="remote acknowledgement receiver frontier",
        )
    except PhysicalWalPromotionGateError:
        reasons.append("REMOTE_ACK_FRONTIER_OR_BLOB_MISMATCH")
    else:
        if receiver_value < target_value:
            reasons.append("REMOTE_ACK_RECEIVER_REPLAY_BEHIND_TARGET")
    return tuple(dict.fromkeys(reasons))


def assess_physical_wal_promotion(
    *,
    prior_activation: VerifiedObjectDeltaRoleMatrixActivation,
    candidate_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    verified_evidence: VerifiedPhysicalWalPromotionEvidence,
    now: datetime,
    verified_remote_ack: object | None = None,
) -> PhysicalWalPromotionAssessment:
    """Assess a proposed FI↔IR writer change without activating anything.

    The function takes no raw receipt, raw role, raw term, raw key, policy,
    frontier, or target.  It derives the only legal source/target pair from a
    verified prior activation and from the signed candidate Witness term.
    """

    try:
        observed_at = _utc(now, label="promotion assessment clock")
    except PhysicalWalPromotionGateError:
        return _blocked("INVALID_ASSESSMENT_CLOCK")
    try:
        context = _active_context(prior_activation, now=observed_at)
    except PhysicalWalPromotionGateError:
        return _blocked("PRIOR_ACTIVATION_UNVERIFIED")
    try:
        candidate = require_live_object_delta_role_matrix_witnessed_term(
            candidate_witnessed_term,
            now=observed_at,
        )
    except ObjectDeltaRoleMatrixRolloverError:
        return _blocked("CANDIDATE_WITNESS_TERM_UNVERIFIED", context=context)
    try:
        facts = _evidence_facts(
            verified_evidence,
            source_public_key=context.source_public_key,
            controller_public_key=context.controller_public_key,
        )
    except PhysicalWalPromotionGateError:
        return _blocked("CONTINUITY_EVIDENCE_UNVERIFIED", context=context)

    source = facts.source.payload
    receiver = facts.receiver.payload
    blob = facts.blob.payload
    continuity = facts.continuity.payload
    reasons: list[str] = []

    if candidate.holder_site != context.target_site:
        reasons.append("CANDIDATE_TERM_DOES_NOT_HOLD_INACTIVE_STANDBY")
    if candidate.writer_epoch <= context.prior_writer_epoch:
        reasons.append("CANDIDATE_TERM_NOT_STRICTLY_NEWER")
    if candidate.writer_lease_id == context.prior_writer_lease_id:
        reasons.append("CANDIDATE_TERM_REUSES_PRIOR_LEASE")
    elif candidate.writer_lease_id in context.historical_writer_lease_ids:
        reasons.append("CANDIDATE_TERM_REUSES_HISTORICAL_LEASE")
    if candidate.witness_transition_id in context.historical_witness_transition_ids:
        reasons.append("CANDIDATE_TERM_REUSES_WITNESS_TRANSITION")

    # A versioned WAL archive is recovery material, not an acknowledgement
    # path.  It may be present in evidence but can never make promotion
    # eligible; only the explicit strict durable/replay contract can do that.
    if (
        source["acknowledgement_mode"]
        != PHYSICAL_WAL_ACKNOWLEDGEMENT_MODE_STRICT_REMOTE_DURABLE_REPLAY
    ):
        reasons.append("SOURCE_ACKNOWLEDGEMENT_NOT_STRICT_REMOTE_DURABLE_REPLAY")
    else:
        reasons.extend(
            _remote_ack_reasons(
                verified_remote_ack,
                context=context,
                facts=facts,
                now=observed_at,
            )
        )

    common_fields = (
        "continuity_id",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "registry_fingerprint",
        "stream_generation_id",
        "baseline_generation_id",
        "baseline_manifest_sha256",
    )
    if any(not _all_equal(_artifact_common_values(facts, field_name)) for field_name in common_fields):
        reasons.append("BASELINE_OR_IDENTITY_BINDING_MISMATCH")
    binding_fields = (
        "source_key_sha256",
        "controller_key_sha256",
        "transport_policy_sha256",
        "route_binding_sha256",
    )
    if any(not _all_equal(_artifact_common_values(facts, field_name)) for field_name in binding_fields):
        reasons.append("EVIDENCE_KEY_OR_POLICY_BINDING_MISMATCH")

    expected_route = {
        "source_site": context.source_site,
        "destination_site": context.target_site,
        "campaign_id": context.campaign_id,
        "release_sha": context.release_sha,
        "registry_fingerprint": context.registry_fingerprint,
        "stream_generation_id": context.stream_generation_id,
        "source_key_sha256": context.source_key_sha256,
        "controller_key_sha256": context.controller_key_sha256,
        "transport_policy_sha256": context.transport_policy_sha256,
        "route_binding_sha256": context.route_binding_sha256,
    }
    for field_name, expected in expected_route.items():
        if any(
            artifact.payload[field_name] != expected
            for artifact in (facts.source, facts.receiver, facts.blob, facts.continuity)
        ):
            reasons.append("CONTINUITY_NOT_BOUND_TO_ACTIVE_ROUTE")
            break

    expected_prior_term = {
        "prior_term_proof_sha256": context.prior_term_proof_sha256,
        "prior_holder_site": context.prior_holder_site,
        "prior_writer_epoch": context.prior_writer_epoch,
        "prior_writer_lease_id": context.prior_writer_lease_id,
    }
    for field_name, expected in expected_prior_term.items():
        if any(
            artifact.payload[field_name] != expected
            for artifact in (facts.source, facts.receiver, facts.blob, facts.continuity)
        ):
            reasons.append("CONTINUITY_NOT_BOUND_TO_PRIOR_TERM")
            break

    if (
        continuity["candidate_term_proof_sha256"] != candidate.proof_sha256
        or continuity["candidate_holder_site"] != candidate.holder_site
        or continuity["candidate_writer_epoch"] != candidate.writer_epoch
        or continuity["candidate_writer_lease_id"] != candidate.writer_lease_id
    ):
        reasons.append("CONTINUITY_NOT_BOUND_TO_CANDIDATE_TERM")

    if (
        receiver["source_durability_receipt_sha256"] != facts.source.sha256
        or blob["source_durability_receipt_sha256"] != facts.source.sha256
        or blob["receiver_replay_receipt_sha256"] != facts.receiver.sha256
        or continuity["source_durability_receipt_sha256"] != facts.source.sha256
        or continuity["receiver_replay_receipt_sha256"] != facts.receiver.sha256
        or continuity["blob_object_receipt_sha256"] != facts.blob.sha256
    ):
        reasons.append("CONTINUITY_ARTIFACT_RECEIPT_BINDING_MISMATCH")

    _baseline_lsn, baseline_lsn = _parse_lsn(source["baseline_wal_lsn"], label="source baseline")
    _source_ack_text, source_ack = _parse_lsn(
        source["acknowledged_durable_wal_lsn"],
        label="source acknowledged frontier",
    )
    _receiver_text, receiver_lsn = _parse_lsn(
        receiver["receiver_replay_wal_lsn"],
        label="receiver replay frontier",
    )
    _blob_text, blob_lsn = _parse_lsn(
        blob["blob_object_frontier_wal_lsn"],
        label="blob object frontier",
    )
    if source_ack < baseline_lsn:
        reasons.append("SOURCE_ACKNOWLEDGED_FRONTIER_PRECEDES_BASELINE")
    if receiver_lsn < source_ack:
        reasons.append("RECEIVER_REPLAY_BEHIND_ACKNOWLEDGED_FRONTIER")
    if blob_lsn < source_ack:
        reasons.append("BLOB_OBJECT_FRONTIER_BEHIND_ACKNOWLEDGED_FRONTIER")
    if blob["objects_complete"] is not True or continuity["objects_complete"] is not True:
        reasons.append("BLOB_OBJECT_FRONTIER_INCOMPLETE")
    if (
        continuity["source_acknowledged_durable_wal_lsn"] != source["acknowledged_durable_wal_lsn"]
        or continuity["receiver_replay_wal_lsn"] != receiver["receiver_replay_wal_lsn"]
        or continuity["blob_object_frontier_wal_lsn"] != blob["blob_object_frontier_wal_lsn"]
    ):
        reasons.append("CONTINUITY_ARTIFACT_FRONTIER_BINDING_MISMATCH")
    if not _evidence_fresh(facts, now=observed_at):
        reasons.append("CONTINUITY_EVIDENCE_STALE_OR_TIME_ORDER_INVALID")

    if reasons:
        return _blocked(*reasons, context=context, facts=facts)
    return _mint_assessment(
        status="eligible",
        reason_codes=(),
        source_site=context.source_site,
        target_site=context.target_site,
        baseline_generation_id=source["baseline_generation_id"],
        acknowledged_durable_wal_lsn=source["acknowledged_durable_wal_lsn"],
        receiver_replay_wal_lsn=receiver["receiver_replay_wal_lsn"],
        blob_object_frontier_wal_lsn=blob["blob_object_frontier_wal_lsn"],
    )


def require_physical_wal_promotion_eligible(
    assessment: object,
) -> PhysicalWalPromotionAssessment:
    """Reject anything except an eligible result; does not start a writer."""

    if type(assessment) is not PhysicalWalPromotionAssessment:
        raise PhysicalWalPromotionGateError("physical WAL promotion assessment is invalid")
    if assessment._capability is not _PHYSICAL_WAL_PROMOTION_ASSESSMENT_CAPABILITY:
        raise PhysicalWalPromotionGateError("physical WAL promotion assessment was not authorized")
    if assessment.status != "eligible" or assessment.reason_codes:
        joined = ",".join(assessment.reason_codes) or "UNKNOWN"
        raise PhysicalWalPromotionGateError(f"physical WAL promotion is blocked: {joined}")
    return assessment


__all__ = (
    "MAX_PHYSICAL_WAL_EVIDENCE_AGE_SECONDS",
    "MAX_PHYSICAL_WAL_EVIDENCE_BYTES",
    "MAX_PHYSICAL_WAL_EVIDENCE_FUTURE_SKEW_SECONDS",
    "PHYSICAL_WAL_ACKNOWLEDGEMENT_MODE_ARCHIVE_ONLY",
    "PHYSICAL_WAL_ACKNOWLEDGEMENT_MODE_STRICT_REMOTE_DURABLE_REPLAY",
    "PHYSICAL_WAL_BLOB_OBJECT_RECEIPT_SCHEMA",
    "PHYSICAL_WAL_CONTINUITY_ARTIFACT_SCHEMA",
    "PHYSICAL_WAL_PROMOTION_GATE_SCHEMA",
    "PHYSICAL_WAL_RECEIVER_REPLAY_RECEIPT_SCHEMA",
    "PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA",
    "PhysicalWalPromotionAssessment",
    "PhysicalWalPromotionGateError",
    "VerifiedPhysicalWalPromotionEvidence",
    "assess_physical_wal_promotion",
    "require_physical_wal_promotion_eligible",
    "require_verified_physical_wal_promotion_evidence",
    "verify_physical_wal_promotion_evidence",
)
