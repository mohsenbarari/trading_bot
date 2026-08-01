"""Durable, pre-CAS acceptance for v2 physical Blob promotion evidence.

The v2 Blob verifier intentionally requires a locally live former-source
Writer-Witness term.  That is correct while collecting evidence, but a
successor normally cannot acquire its term until the former term has expired
or been revoked.  This module bridges those two moments without relaxing the
v2 verifier:

* before successor CAS, it revalidates the v2 requirement while the former
  term is live and binds its exact facts to the active predecessor route;
* it asks an explicitly injected append-and-read-back authority to durably
  append one canonical acceptance record; and
* later callers can reverify the authority-signed receipt using only an
  independently pinned public key.  That recheck does not contact or require
  the former source.

This module has no database, network, filesystem, Object Storage, Docker,
SSH, or deployment client.  Durability is an explicit trust boundary of the
injected authority; the code verifies its signed readback receipt but cannot
itself prove where that authority persisted the record.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    OBJECT_KEY_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    VERSION_ID_RE,
    WEBAPP_SITES,
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
from core.physical_blob_object_storage_uploader import (
    VerifiedPhysicalBlobObjectStorageBinding,
)
from core.physical_blob_receiver_promotion_evidence import (
    PHYSICAL_WAL_PROMOTION_V2_BLOB_REQUIREMENT_SCHEMA,
    PhysicalBlobReceiverPromotionEvidenceConfig,
    PhysicalBlobReceiverPromotionEvidenceError,
    VerifiedPhysicalBlobReceiverPromotionEvidence,
    VerifiedPhysicalWalPromotionV2BlobRequirement,
    require_physical_wal_promotion_v2_blob_requirement,
)
from core.physical_wal_promotion_gate import (
    PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA,
    PhysicalWalPromotionGateError,
    VerifiedPhysicalWalPromotionEvidence,
    require_verified_physical_wal_promotion_evidence,
)


__all__ = (
    "MAX_PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_AGE_SECONDS",
    "PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_DEFAULT_ENABLED",
    "PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_RECEIPT_SCHEMA",
    "PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_SCHEMA",
    "PhysicalBlobPreCasAcceptanceAuthority",
    "PhysicalBlobPreCasAcceptanceConfig",
    "PhysicalBlobPreCasAcceptanceError",
    "VerifiedPhysicalBlobPreCasAcceptance",
    "persist_physical_blob_pre_cas_acceptance",
    "require_verified_physical_blob_pre_cas_acceptance",
    "verify_physical_blob_pre_cas_acceptance",
)


PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_SCHEMA = (
    "gold-trade-physical-blob-pre-cas-acceptance-v1"
)
PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_RECEIPT_SCHEMA = (
    "gold-trade-physical-blob-pre-cas-acceptance-receipt-v1"
)
PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_DEFAULT_ENABLED = False
MAX_PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_AGE_SECONDS = 600
MAX_PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_FUTURE_SKEW_SECONDS = 5

_MAX_ACCEPTANCE_BYTES = 32 * 1024
_MAX_RECEIPT_BYTES = 32 * 1024
_VERIFIED_ACCEPTANCE_CAPABILITY = object()
_ACCEPTANCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$", re.ASCII)
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_WITNESS_TRANSITION_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    re.ASCII,
)

_ACCEPTANCE_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "pre_cas_operation_id",
        "source_site",
        "destination_site",
        "campaign_id",
        "release_sha",
        "stream_generation_id",
        "baseline_generation_id",
        "baseline_manifest_sha256",
        "baseline_wal_lsn",
        "destination_age_recipient",
        "former_writer_epoch",
        "former_writer_lease_id",
        "former_witness_transition_id",
        "former_witnessed_term_proof_sha256",
        "source_evidence_schema",
        "source_evidence_sha256",
        "blob_timeline_id",
        "blob_route_binding_sha256",
        "blob_mapping_plaintext_sha256",
        "blob_mapping_receipt_sha256",
        "blob_mapping_object_key",
        "blob_mapping_object_version_id",
        "blob_mapping_ciphertext_sha256",
        "blob_mapping_ciphertext_bytes",
        "original_v1_inventory_receipt_sha256",
        "blob_receipts_sha256",
        "blob_entry_count",
        "blob_mapping_eligible_replay_wal_lsn",
        "accepted_at",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "pre_cas_operation_id",
        "acceptance_sha256",
        "readback_acceptance_sha256",
        "append_sequence",
        "accepted_at",
        "issued_at",
        "signature",
    }
)


class PhysicalBlobPreCasAcceptanceError(ValueError):
    """A durable pre-CAS Blob acceptance is invalid, stale, or unbound."""


class PhysicalBlobPreCasAcceptanceAuthority(Protocol):
    """Injected durable append-only authority.

    An implementation must atomically reject reuse of ``pre_cas_operation_id``,
    append the exact canonical acceptance bytes, read the exact bytes back,
    and only then return an authority-signed canonical receipt.  The receipt
    is verified locally against the config's independently pinned public key.
    """

    def append_and_read_back(
        self,
        *,
        canonical_acceptance: bytes,
        acceptance_sha256: str,
    ) -> bytes:
        """Durably append exactly one record and return its signed readback receipt."""


@dataclass(frozen=True)
class PhysicalBlobPreCasAcceptanceConfig:
    """Public pin and bounded freshness policy for acceptance readback."""

    authority_public_key: bytes = b""
    enabled: bool = PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_DEFAULT_ENABLED
    maximum_acceptance_age_seconds: int = MAX_PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_AGE_SECONDS
    maximum_future_skew_seconds: int = MAX_PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_FUTURE_SKEW_SECONDS


@dataclass(frozen=True)
class VerifiedPhysicalBlobPreCasAcceptance:
    """Opaque, independently re-verifiable durable predecessor evidence.

    It is intentionally not a current writer permit.  It preserves exactly
    the predecessor-bound v2 Blob facts needed after the predecessor expires,
    but contains no live source binding or client handle.
    """

    canonical_acceptance: bytes
    signed_authority_receipt: bytes
    authority_public_key: bytes
    pre_cas_operation_id: str
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    destination_age_recipient: str
    former_writer_epoch: int
    former_writer_lease_id: str
    former_witness_transition_id: str
    former_witnessed_term_proof_sha256: str
    source_evidence_schema: str
    source_evidence_sha256: str
    blob_timeline_id: int
    blob_route_binding_sha256: str
    blob_mapping_plaintext_sha256: str
    blob_mapping_receipt_sha256: str
    blob_mapping_object_key: str
    blob_mapping_object_version_id: str
    blob_mapping_ciphertext_sha256: str
    blob_mapping_ciphertext_bytes: int
    original_v1_inventory_receipt_sha256: str
    blob_receipts_sha256: str
    blob_entry_count: int
    blob_mapping_eligible_replay_wal_lsn: str
    accepted_at: datetime
    authority_receipt_sha256: str
    authority_append_sequence: int
    authority_issued_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _ConfigFacts:
    authority_public_key: bytes
    maximum_acceptance_age_seconds: int
    maximum_future_skew_seconds: int


@dataclass(frozen=True)
class _PriorFacts:
    activation: VerifiedObjectDeltaRoleMatrixActivation
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    destination_age_recipient: str
    former_writer_epoch: int
    former_writer_lease_id: str
    former_witness_transition_id: str
    former_witnessed_term_proof_sha256: str


@dataclass(frozen=True)
class _WalSourceFacts:
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    former_writer_epoch: int
    former_writer_lease_id: str
    former_witnessed_term_proof_sha256: str
    source_evidence_schema: str
    source_evidence_sha256: str


@dataclass(frozen=True)
class _V2Facts:
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    destination_age_recipient: str
    former_writer_epoch: int
    former_writer_lease_id: str
    former_witnessed_term_proof_sha256: str
    blob_timeline_id: int
    blob_route_binding_sha256: str
    blob_mapping_plaintext_sha256: str
    blob_mapping_receipt_sha256: str
    blob_mapping_object_key: str
    blob_mapping_object_version_id: str
    blob_mapping_ciphertext_sha256: str
    blob_mapping_ciphertext_bytes: int
    original_v1_inventory_receipt_sha256: str
    blob_receipts_sha256: str
    blob_entry_count: int
    blob_mapping_eligible_replay_wal_lsn: str


@dataclass(frozen=True)
class _AcceptanceFacts:
    canonical_acceptance: bytes
    pre_cas_operation_id: str
    source_site: str
    destination_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    destination_age_recipient: str
    former_writer_epoch: int
    former_writer_lease_id: str
    former_witness_transition_id: str
    former_witnessed_term_proof_sha256: str
    source_evidence_schema: str
    source_evidence_sha256: str
    blob_timeline_id: int
    blob_route_binding_sha256: str
    blob_mapping_plaintext_sha256: str
    blob_mapping_receipt_sha256: str
    blob_mapping_object_key: str
    blob_mapping_object_version_id: str
    blob_mapping_ciphertext_sha256: str
    blob_mapping_ciphertext_bytes: int
    original_v1_inventory_receipt_sha256: str
    blob_receipts_sha256: str
    blob_entry_count: int
    blob_mapping_eligible_replay_wal_lsn: str
    accepted_at: datetime


@dataclass(frozen=True)
class _ReceiptFacts:
    signed_receipt: bytes
    receipt_sha256: str
    pre_cas_operation_id: str
    acceptance_sha256: str
    append_sequence: int
    accepted_at: datetime
    issued_at: datetime


def _fail(reason_code: str) -> None:
    raise PhysicalBlobPreCasAcceptanceError(reason_code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("PRE_CAS_ACCEPTANCE_JSON_DUPLICATE_FIELD")
        result[key] = value
    return result


def _canonical_json(raw: object, *, label: str, maximum_bytes: int) -> tuple[dict[str, Any], bytes]:
    if isinstance(raw, Mapping):
        try:
            canonical = canonical_json_bytes(dict(raw))
        except (TypeError, ValueError):
            _fail(f"{label}_INVALID")
        payload = dict(raw)
    elif isinstance(raw, bytes) and raw and len(raw) <= maximum_bytes:
        canonical = raw
        try:
            payload = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_strict_object)
        except (UnicodeDecodeError, json.JSONDecodeError):
            _fail(f"{label}_INVALID")
        if not isinstance(payload, dict):
            _fail(f"{label}_INVALID")
        try:
            if canonical_json_bytes(payload) != raw:
                _fail(f"{label}_NOT_CANONICAL")
        except (TypeError, ValueError):
            _fail(f"{label}_INVALID")
    else:
        _fail(f"{label}_INVALID")
    if not canonical or len(canonical) > maximum_bytes:
        _fail(f"{label}_INVALID")
    return payload, canonical


def _utc(value: object, *, reason_code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(reason_code)
    return value.astimezone(timezone.utc)


def _timestamp(value: object, *, reason_code: str) -> datetime:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail(reason_code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(reason_code)
    if parsed.tzinfo is None:
        _fail(reason_code)
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        _fail(reason_code)
    return normalized


def _site(value: object, *, reason_code: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        _fail(reason_code)
    return value


def _text(value: object, *, pattern: re.Pattern[str], reason_code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(reason_code)
    return value


def _sha256(value: object, *, reason_code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(reason_code)
    return value


def _positive_int(value: object, *, maximum: int, reason_code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(reason_code)
    return value


def _lsn(value: object, *, reason_code: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        _fail(reason_code)
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _public_key(value: object, *, reason_code: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32 or value == b"\x00" * 32:
        _fail(reason_code)
    try:
        Ed25519PublicKey.from_public_bytes(value)
    except ValueError:
        _fail(reason_code)
    return value


def _config(value: object) -> _ConfigFacts:
    if type(value) is not PhysicalBlobPreCasAcceptanceConfig:
        _fail("PRE_CAS_ACCEPTANCE_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("PRE_CAS_ACCEPTANCE_DISABLED")
    maximum_age = _positive_int(
        value.maximum_acceptance_age_seconds,
        maximum=3600,
        reason_code="PRE_CAS_ACCEPTANCE_MAX_AGE_INVALID",
    )
    future_skew = _positive_int(
        value.maximum_future_skew_seconds,
        maximum=60,
        reason_code="PRE_CAS_ACCEPTANCE_FUTURE_SKEW_INVALID",
    )
    if future_skew >= maximum_age:
        _fail("PRE_CAS_ACCEPTANCE_FRESHNESS_POLICY_INVALID")
    return _ConfigFacts(
        authority_public_key=_public_key(
            value.authority_public_key,
            reason_code="PRE_CAS_ACCEPTANCE_AUTHORITY_KEY_INVALID",
        ),
        maximum_acceptance_age_seconds=maximum_age,
        maximum_future_skew_seconds=future_skew,
    )


def _prior_facts(
    *,
    prior_activation: object,
    former_witnessed_term: object,
    now: datetime,
) -> _PriorFacts:
    try:
        activation = require_verified_object_delta_role_matrix_activation(
            prior_activation,
            now=now,
        )
        matrix = require_verified_object_delta_role_matrix(activation._matrix)
        activation_term = require_verified_object_delta_role_matrix_witnessed_term(
            activation._witnessed_term,
            now=now,
        )
        former = require_live_object_delta_role_matrix_witnessed_term(
            former_witnessed_term,
            now=now,
        )
        route = active_object_delta_role_matrix_route(matrix)
        source_binding = route.source_pin.binding
        source_role = object_delta_role_matrix_site_role(matrix, site=source_binding.source_site)
        target_role = object_delta_role_matrix_site_role(
            matrix,
            site=source_binding.destination_site,
        )
    except (AttributeError, ObjectDeltaRoleMatrixError, ObjectDeltaRoleMatrixRolloverError):
        _fail("PRE_CAS_PRIOR_ACTIVATION_OR_FORMER_TERM_UNVERIFIED")
    source_site = _site(source_binding.source_site, reason_code="PRE_CAS_PRIOR_ROUTE_INVALID")
    destination_site = _site(
        source_binding.destination_site,
        reason_code="PRE_CAS_PRIOR_ROUTE_INVALID",
    )
    if source_site == destination_site:
        _fail("PRE_CAS_PRIOR_ROUTE_INVALID")
    if (
        source_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE
        or target_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER
    ):
        _fail("PRE_CAS_PRIOR_ROUTE_DIRECTION_INVALID")
    activation_projection = (
        activation_term.holder_site,
        activation_term.writer_epoch,
        activation_term.writer_lease_id,
        activation_term.witness_transition_id,
        activation_term.proof_sha256,
    )
    former_projection = (
        former.holder_site,
        former.writer_epoch,
        former.writer_lease_id,
        former.witness_transition_id,
        former.proof_sha256,
    )
    if activation_projection != former_projection or former.holder_site != source_site:
        _fail("PRE_CAS_FORMER_TERM_NOT_ACTIVE_PREDECESSOR")
    policy = route.source_pin.transport_policy
    destination_age_recipient = (
        policy.webapp_fi_age_recipient
        if destination_site == "webapp_fi"
        else policy.webapp_ir_age_recipient
    )
    if not isinstance(destination_age_recipient, str) or not destination_age_recipient:
        _fail("PRE_CAS_DESTINATION_RECIPIENT_INVALID")
    return _PriorFacts(
        activation=activation,
        term=former,
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=_text(
            source_binding.campaign_id,
            pattern=CAMPAIGN_ID_RE,
            reason_code="PRE_CAS_PRIOR_ROUTE_INVALID",
        ),
        release_sha=_text(
            source_binding.release_sha,
            pattern=RELEASE_SHA_RE,
            reason_code="PRE_CAS_PRIOR_ROUTE_INVALID",
        ),
        stream_generation_id=_text(
            source_binding.stream_generation_id,
            pattern=STREAM_GENERATION_ID_RE,
            reason_code="PRE_CAS_PRIOR_ROUTE_INVALID",
        ),
        destination_age_recipient=destination_age_recipient,
        former_writer_epoch=_positive_int(
            former.writer_epoch,
            maximum=2**63 - 1,
            reason_code="PRE_CAS_FORMER_TERM_EPOCH_INVALID",
        ),
        former_writer_lease_id=_text(
            former.writer_lease_id,
            pattern=LEASE_ID_RE,
            reason_code="PRE_CAS_FORMER_TERM_LEASE_INVALID",
        ),
        former_witness_transition_id=_text(
            former.witness_transition_id,
            pattern=_WITNESS_TRANSITION_ID_RE,
            reason_code="PRE_CAS_FORMER_TERM_TRANSITION_INVALID",
        ),
        former_witnessed_term_proof_sha256=_sha256(
            former.proof_sha256,
            reason_code="PRE_CAS_FORMER_TERM_PROOF_INVALID",
        ),
    )


def _wal_source_facts(value: object) -> _WalSourceFacts:
    try:
        evidence = require_verified_physical_wal_promotion_evidence(value)
    except PhysicalWalPromotionGateError:
        _fail("PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED")
    payload, raw = _canonical_json(
        evidence.source_durability_receipt,
        label="PRE_CAS_SOURCE_EVIDENCE",
        maximum_bytes=_MAX_ACCEPTANCE_BYTES,
    )
    if set(payload) != {
        "schema",
        "kind",
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
        "acknowledgement_mode",
        "baseline_wal_lsn",
        "acknowledged_durable_wal_lsn",
        "observed_at",
        "signature",
    }:
        _fail("PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED")
    if (
        payload.get("schema") != PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA
        or payload.get("kind") != "source_durable_wal_frontier"
    ):
        _fail("PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED")
    return _WalSourceFacts(
        source_site=_site(payload.get("source_site"), reason_code="PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED"),
        destination_site=_site(
            payload.get("destination_site"),
            reason_code="PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED",
        ),
        campaign_id=_text(
            payload.get("campaign_id"),
            pattern=CAMPAIGN_ID_RE,
            reason_code="PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED",
        ),
        release_sha=_text(
            payload.get("release_sha"),
            pattern=RELEASE_SHA_RE,
            reason_code="PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED",
        ),
        stream_generation_id=_text(
            payload.get("stream_generation_id"),
            pattern=STREAM_GENERATION_ID_RE,
            reason_code="PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED",
        ),
        baseline_generation_id=_text(
            payload.get("baseline_generation_id"),
            pattern=STREAM_GENERATION_ID_RE,
            reason_code="PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED",
        ),
        baseline_manifest_sha256=_sha256(
            payload.get("baseline_manifest_sha256"),
            reason_code="PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED",
        ),
        baseline_wal_lsn=_lsn(
            payload.get("baseline_wal_lsn"),
            reason_code="PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED",
        )[0],
        former_writer_epoch=_positive_int(
            payload.get("prior_writer_epoch"),
            maximum=2**63 - 1,
            reason_code="PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED",
        ),
        former_writer_lease_id=_text(
            payload.get("prior_writer_lease_id"),
            pattern=LEASE_ID_RE,
            reason_code="PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED",
        ),
        former_witnessed_term_proof_sha256=_sha256(
            payload.get("prior_term_proof_sha256"),
            reason_code="PRE_CAS_SOURCE_EVIDENCE_UNVERIFIED",
        ),
        source_evidence_schema=PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA,
        source_evidence_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _v2_facts(
    value: object,
    *,
    blob_evidence_config: object,
    verified_blob_binding: object,
    now: datetime,
) -> _V2Facts:
    try:
        requirement = require_physical_wal_promotion_v2_blob_requirement(
            value,
            config=blob_evidence_config,
            verified_binding=verified_blob_binding,
            now=now,
        )
    except PhysicalBlobReceiverPromotionEvidenceError:
        _fail("PRE_CAS_V2_BLOB_REQUIREMENT_UNVERIFIED")
    if (
        type(requirement) is not VerifiedPhysicalWalPromotionV2BlobRequirement
        or requirement.schema != PHYSICAL_WAL_PROMOTION_V2_BLOB_REQUIREMENT_SCHEMA
        or type(requirement.receiver_promotion_evidence)
        is not VerifiedPhysicalBlobReceiverPromotionEvidence
    ):
        _fail("PRE_CAS_V2_BLOB_REQUIREMENT_UNVERIFIED")
    evidence = requirement.receiver_promotion_evidence
    mapping_replay_lsn, _ = _lsn(
        requirement.mapping_eligible_replay_wal_lsn,
        reason_code="PRE_CAS_V2_BLOB_REPLAY_LSN_INVALID",
    )
    baseline_wal_lsn, _ = _lsn(
        requirement.baseline_wal_lsn,
        reason_code="PRE_CAS_V2_BLOB_BASELINE_LSN_INVALID",
    )
    if mapping_replay_lsn != baseline_wal_lsn:
        _fail("PRE_CAS_V2_BLOB_BASELINE_REPLAY_SCOPE_MISMATCH")
    recipient = evidence.destination_age_recipient
    if not isinstance(recipient, str) or not recipient:
        _fail("PRE_CAS_V2_BLOB_REQUIREMENT_UNVERIFIED")
    return _V2Facts(
        source_site=_site(requirement.source_site, reason_code="PRE_CAS_V2_BLOB_ROUTE_INVALID"),
        destination_site=_site(
            requirement.destination_site,
            reason_code="PRE_CAS_V2_BLOB_ROUTE_INVALID",
        ),
        campaign_id=_text(
            requirement.campaign_id,
            pattern=CAMPAIGN_ID_RE,
            reason_code="PRE_CAS_V2_BLOB_IDENTITY_INVALID",
        ),
        release_sha=_text(
            requirement.release_sha,
            pattern=RELEASE_SHA_RE,
            reason_code="PRE_CAS_V2_BLOB_IDENTITY_INVALID",
        ),
        baseline_generation_id=_text(
            requirement.baseline_generation_id,
            pattern=STREAM_GENERATION_ID_RE,
            reason_code="PRE_CAS_V2_BLOB_BASELINE_INVALID",
        ),
        baseline_manifest_sha256=_sha256(
            requirement.baseline_manifest_sha256,
            reason_code="PRE_CAS_V2_BLOB_BASELINE_INVALID",
        ),
        baseline_wal_lsn=baseline_wal_lsn,
        destination_age_recipient=recipient,
        former_writer_epoch=_positive_int(
            requirement.writer_epoch,
            maximum=2**63 - 1,
            reason_code="PRE_CAS_V2_BLOB_WRITER_EPOCH_INVALID",
        ),
        former_writer_lease_id=_text(
            requirement.writer_lease_id,
            pattern=LEASE_ID_RE,
            reason_code="PRE_CAS_V2_BLOB_WRITER_LEASE_INVALID",
        ),
        former_witnessed_term_proof_sha256=_sha256(
            requirement.witnessed_term_proof_sha256,
            reason_code="PRE_CAS_V2_BLOB_WRITER_PROOF_INVALID",
        ),
        blob_timeline_id=_positive_int(
            requirement.timeline_id,
            maximum=0xFFFFFFFF,
            reason_code="PRE_CAS_V2_BLOB_TIMELINE_INVALID",
        ),
        blob_route_binding_sha256=_sha256(
            requirement.route_binding_sha256,
            reason_code="PRE_CAS_V2_BLOB_ROUTE_BINDING_INVALID",
        ),
        blob_mapping_plaintext_sha256=_sha256(
            requirement.mapping_plaintext_sha256,
            reason_code="PRE_CAS_V2_BLOB_MAPPING_PLAINTEXT_INVALID",
        ),
        blob_mapping_receipt_sha256=_sha256(
            requirement.mapping_receipt_sha256,
            reason_code="PRE_CAS_V2_BLOB_MAPPING_RECEIPT_INVALID",
        ),
        blob_mapping_object_key=_text(
            requirement.mapping_object_key,
            pattern=OBJECT_KEY_RE,
            reason_code="PRE_CAS_V2_BLOB_MAPPING_OBJECT_INVALID",
        ),
        blob_mapping_object_version_id=_text(
            requirement.mapping_object_version_id,
            pattern=VERSION_ID_RE,
            reason_code="PRE_CAS_V2_BLOB_MAPPING_VERSION_INVALID",
        ),
        blob_mapping_ciphertext_sha256=_sha256(
            requirement.mapping_ciphertext_sha256,
            reason_code="PRE_CAS_V2_BLOB_MAPPING_CIPHERTEXT_INVALID",
        ),
        blob_mapping_ciphertext_bytes=_positive_int(
            requirement.mapping_ciphertext_bytes,
            maximum=100 * 1024 * 1024,
            reason_code="PRE_CAS_V2_BLOB_MAPPING_CIPHERTEXT_BYTES_INVALID",
        ),
        original_v1_inventory_receipt_sha256=_sha256(
            requirement.original_v1_inventory_receipt_sha256,
            reason_code="PRE_CAS_V2_BLOB_INVENTORY_RECEIPT_INVALID",
        ),
        blob_receipts_sha256=_sha256(
            requirement.blob_receipts_sha256,
            reason_code="PRE_CAS_V2_BLOB_RECEIPT_SET_INVALID",
        ),
        blob_entry_count=_positive_int(
            requirement.entry_count,
            maximum=16_384,
            reason_code="PRE_CAS_V2_BLOB_ENTRY_COUNT_INVALID",
        ),
        blob_mapping_eligible_replay_wal_lsn=mapping_replay_lsn,
    )


def _cross_bind_pre_cas(*, prior: _PriorFacts, source: _WalSourceFacts, blob: _V2Facts) -> None:
    if (
        source.source_site != prior.source_site
        or source.destination_site != prior.destination_site
        or source.campaign_id != prior.campaign_id
        or source.release_sha != prior.release_sha
        or source.stream_generation_id != prior.stream_generation_id
    ):
        _fail("PRE_CAS_SOURCE_EVIDENCE_ROUTE_MISMATCH")
    if (
        source.former_writer_epoch != prior.former_writer_epoch
        or source.former_writer_lease_id != prior.former_writer_lease_id
        or source.former_witnessed_term_proof_sha256
        != prior.former_witnessed_term_proof_sha256
    ):
        _fail("PRE_CAS_SOURCE_EVIDENCE_FORMER_TERM_MISMATCH")
    if (
        blob.source_site != prior.source_site
        or blob.destination_site != prior.destination_site
        or blob.campaign_id != prior.campaign_id
        or blob.release_sha != prior.release_sha
        or blob.destination_age_recipient != prior.destination_age_recipient
    ):
        _fail("PRE_CAS_V2_BLOB_ROUTE_MISMATCH")
    if (
        blob.baseline_generation_id != source.baseline_generation_id
        or blob.baseline_manifest_sha256 != source.baseline_manifest_sha256
        or blob.baseline_wal_lsn != source.baseline_wal_lsn
    ):
        _fail("PRE_CAS_V2_BLOB_BASELINE_MISMATCH")
    if (
        blob.former_writer_epoch != prior.former_writer_epoch
        or blob.former_writer_lease_id != prior.former_writer_lease_id
        or blob.former_witnessed_term_proof_sha256
        != prior.former_witnessed_term_proof_sha256
    ):
        _fail("PRE_CAS_V2_BLOB_FORMER_TERM_MISMATCH")


def _acceptance_payload(
    *,
    pre_cas_operation_id: object,
    prior: _PriorFacts,
    source: _WalSourceFacts,
    blob: _V2Facts,
    accepted_at: datetime,
) -> dict[str, Any]:
    return {
        "schema": PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_SCHEMA,
        "kind": "durable_pre_cas_v2_blob_acceptance",
        "pre_cas_operation_id": _text(
            pre_cas_operation_id,
            pattern=_ACCEPTANCE_ID_RE,
            reason_code="PRE_CAS_OPERATION_ID_INVALID",
        ),
        "source_site": prior.source_site,
        "destination_site": prior.destination_site,
        "campaign_id": prior.campaign_id,
        "release_sha": prior.release_sha,
        "stream_generation_id": prior.stream_generation_id,
        "baseline_generation_id": source.baseline_generation_id,
        "baseline_manifest_sha256": source.baseline_manifest_sha256,
        "baseline_wal_lsn": source.baseline_wal_lsn,
        "destination_age_recipient": prior.destination_age_recipient,
        "former_writer_epoch": prior.former_writer_epoch,
        "former_writer_lease_id": prior.former_writer_lease_id,
        "former_witness_transition_id": prior.former_witness_transition_id,
        "former_witnessed_term_proof_sha256": prior.former_witnessed_term_proof_sha256,
        "source_evidence_schema": source.source_evidence_schema,
        "source_evidence_sha256": source.source_evidence_sha256,
        "blob_timeline_id": blob.blob_timeline_id,
        "blob_route_binding_sha256": blob.blob_route_binding_sha256,
        "blob_mapping_plaintext_sha256": blob.blob_mapping_plaintext_sha256,
        "blob_mapping_receipt_sha256": blob.blob_mapping_receipt_sha256,
        "blob_mapping_object_key": blob.blob_mapping_object_key,
        "blob_mapping_object_version_id": blob.blob_mapping_object_version_id,
        "blob_mapping_ciphertext_sha256": blob.blob_mapping_ciphertext_sha256,
        "blob_mapping_ciphertext_bytes": blob.blob_mapping_ciphertext_bytes,
        "original_v1_inventory_receipt_sha256": blob.original_v1_inventory_receipt_sha256,
        "blob_receipts_sha256": blob.blob_receipts_sha256,
        "blob_entry_count": blob.blob_entry_count,
        "blob_mapping_eligible_replay_wal_lsn": blob.blob_mapping_eligible_replay_wal_lsn,
        "accepted_at": accepted_at.isoformat(),
    }


def _acceptance_facts(value: object, *, config: _ConfigFacts, now: datetime) -> _AcceptanceFacts:
    payload, canonical = _canonical_json(
        value,
        label="PRE_CAS_ACCEPTANCE",
        maximum_bytes=_MAX_ACCEPTANCE_BYTES,
    )
    if (
        set(payload) != _ACCEPTANCE_FIELDS
        or payload.get("schema") != PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_SCHEMA
        or payload.get("kind") != "durable_pre_cas_v2_blob_acceptance"
    ):
        _fail("PRE_CAS_ACCEPTANCE_SCHEMA_INVALID")
    accepted_at = _timestamp(payload.get("accepted_at"), reason_code="PRE_CAS_ACCEPTANCE_TIMESTAMP_INVALID")
    oldest = now - timedelta(seconds=config.maximum_acceptance_age_seconds)
    newest = now + timedelta(seconds=config.maximum_future_skew_seconds)
    if accepted_at < oldest or accepted_at > newest:
        _fail("PRE_CAS_ACCEPTANCE_STALE_OR_FUTURE")
    source_site = _site(payload.get("source_site"), reason_code="PRE_CAS_ACCEPTANCE_ROUTE_INVALID")
    destination_site = _site(
        payload.get("destination_site"),
        reason_code="PRE_CAS_ACCEPTANCE_ROUTE_INVALID",
    )
    if source_site == destination_site:
        _fail("PRE_CAS_ACCEPTANCE_ROUTE_INVALID")
    baseline_wal_lsn, _ = _lsn(
        payload.get("baseline_wal_lsn"),
        reason_code="PRE_CAS_ACCEPTANCE_BASELINE_INVALID",
    )
    mapping_replay_lsn, _ = _lsn(
        payload.get("blob_mapping_eligible_replay_wal_lsn"),
        reason_code="PRE_CAS_ACCEPTANCE_BLOB_INVENTORY_INVALID",
    )
    if mapping_replay_lsn != baseline_wal_lsn:
        _fail("PRE_CAS_ACCEPTANCE_BLOB_REPLAY_SCOPE_MISMATCH")
    if payload.get("source_evidence_schema") != PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA:
        _fail("PRE_CAS_ACCEPTANCE_SOURCE_EVIDENCE_VERSION_INVALID")
    recipient = payload.get("destination_age_recipient")
    if not isinstance(recipient, str) or not recipient:
        _fail("PRE_CAS_ACCEPTANCE_ROUTE_INVALID")
    return _AcceptanceFacts(
        canonical_acceptance=canonical,
        pre_cas_operation_id=_text(
            payload.get("pre_cas_operation_id"),
            pattern=_ACCEPTANCE_ID_RE,
            reason_code="PRE_CAS_ACCEPTANCE_OPERATION_ID_INVALID",
        ),
        source_site=source_site,
        destination_site=destination_site,
        campaign_id=_text(payload.get("campaign_id"), pattern=CAMPAIGN_ID_RE, reason_code="PRE_CAS_ACCEPTANCE_ROUTE_INVALID"),
        release_sha=_text(payload.get("release_sha"), pattern=RELEASE_SHA_RE, reason_code="PRE_CAS_ACCEPTANCE_ROUTE_INVALID"),
        stream_generation_id=_text(payload.get("stream_generation_id"), pattern=STREAM_GENERATION_ID_RE, reason_code="PRE_CAS_ACCEPTANCE_ROUTE_INVALID"),
        baseline_generation_id=_text(payload.get("baseline_generation_id"), pattern=STREAM_GENERATION_ID_RE, reason_code="PRE_CAS_ACCEPTANCE_BASELINE_INVALID"),
        baseline_manifest_sha256=_sha256(payload.get("baseline_manifest_sha256"), reason_code="PRE_CAS_ACCEPTANCE_BASELINE_INVALID"),
        baseline_wal_lsn=baseline_wal_lsn,
        destination_age_recipient=recipient,
        former_writer_epoch=_positive_int(payload.get("former_writer_epoch"), maximum=2**63 - 1, reason_code="PRE_CAS_ACCEPTANCE_FORMER_TERM_INVALID"),
        former_writer_lease_id=_text(payload.get("former_writer_lease_id"), pattern=LEASE_ID_RE, reason_code="PRE_CAS_ACCEPTANCE_FORMER_TERM_INVALID"),
        former_witness_transition_id=_text(payload.get("former_witness_transition_id"), pattern=_WITNESS_TRANSITION_ID_RE, reason_code="PRE_CAS_ACCEPTANCE_FORMER_TERM_INVALID"),
        former_witnessed_term_proof_sha256=_sha256(payload.get("former_witnessed_term_proof_sha256"), reason_code="PRE_CAS_ACCEPTANCE_FORMER_TERM_INVALID"),
        source_evidence_schema=PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA,
        source_evidence_sha256=_sha256(payload.get("source_evidence_sha256"), reason_code="PRE_CAS_ACCEPTANCE_SOURCE_EVIDENCE_INVALID"),
        blob_timeline_id=_positive_int(payload.get("blob_timeline_id"), maximum=0xFFFFFFFF, reason_code="PRE_CAS_ACCEPTANCE_BLOB_INVENTORY_INVALID"),
        blob_route_binding_sha256=_sha256(payload.get("blob_route_binding_sha256"), reason_code="PRE_CAS_ACCEPTANCE_BLOB_INVENTORY_INVALID"),
        blob_mapping_plaintext_sha256=_sha256(payload.get("blob_mapping_plaintext_sha256"), reason_code="PRE_CAS_ACCEPTANCE_BLOB_INVENTORY_INVALID"),
        blob_mapping_receipt_sha256=_sha256(payload.get("blob_mapping_receipt_sha256"), reason_code="PRE_CAS_ACCEPTANCE_BLOB_INVENTORY_INVALID"),
        blob_mapping_object_key=_text(payload.get("blob_mapping_object_key"), pattern=OBJECT_KEY_RE, reason_code="PRE_CAS_ACCEPTANCE_BLOB_INVENTORY_INVALID"),
        blob_mapping_object_version_id=_text(payload.get("blob_mapping_object_version_id"), pattern=VERSION_ID_RE, reason_code="PRE_CAS_ACCEPTANCE_BLOB_INVENTORY_INVALID"),
        blob_mapping_ciphertext_sha256=_sha256(payload.get("blob_mapping_ciphertext_sha256"), reason_code="PRE_CAS_ACCEPTANCE_BLOB_INVENTORY_INVALID"),
        blob_mapping_ciphertext_bytes=_positive_int(payload.get("blob_mapping_ciphertext_bytes"), maximum=100 * 1024 * 1024, reason_code="PRE_CAS_ACCEPTANCE_BLOB_INVENTORY_INVALID"),
        original_v1_inventory_receipt_sha256=_sha256(payload.get("original_v1_inventory_receipt_sha256"), reason_code="PRE_CAS_ACCEPTANCE_BLOB_INVENTORY_INVALID"),
        blob_receipts_sha256=_sha256(payload.get("blob_receipts_sha256"), reason_code="PRE_CAS_ACCEPTANCE_BLOB_INVENTORY_INVALID"),
        blob_entry_count=_positive_int(payload.get("blob_entry_count"), maximum=16_384, reason_code="PRE_CAS_ACCEPTANCE_BLOB_INVENTORY_INVALID"),
        blob_mapping_eligible_replay_wal_lsn=mapping_replay_lsn,
        accepted_at=accepted_at,
    )


def _decode_signature(value: object) -> bytes:
    if not isinstance(value, str):
        _fail("PRE_CAS_ACCEPTANCE_RECEIPT_SIGNATURE_INVALID")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError):
        _fail("PRE_CAS_ACCEPTANCE_RECEIPT_SIGNATURE_INVALID")
    if len(decoded) != 64:
        _fail("PRE_CAS_ACCEPTANCE_RECEIPT_SIGNATURE_INVALID")
    return decoded


def _receipt_facts(
    value: object,
    *,
    acceptance: _AcceptanceFacts,
    config: _ConfigFacts,
    now: datetime,
) -> _ReceiptFacts:
    payload, raw = _canonical_json(
        value,
        label="PRE_CAS_ACCEPTANCE_RECEIPT",
        maximum_bytes=_MAX_RECEIPT_BYTES,
    )
    if (
        set(payload) != _RECEIPT_FIELDS
        or payload.get("schema") != PHYSICAL_BLOB_PRE_CAS_ACCEPTANCE_RECEIPT_SCHEMA
        or payload.get("kind") != "durable_pre_cas_v2_blob_acceptance_readback"
    ):
        _fail("PRE_CAS_ACCEPTANCE_RECEIPT_SCHEMA_INVALID")
    signature = _decode_signature(payload.get("signature"))
    unsigned = {key: item for key, item in payload.items() if key != "signature"}
    try:
        Ed25519PublicKey.from_public_bytes(config.authority_public_key).verify(
            signature,
            canonical_json_bytes(unsigned),
        )
    except (InvalidSignature, ValueError):
        _fail("PRE_CAS_ACCEPTANCE_RECEIPT_SIGNATURE_INVALID")
    operation_id = _text(
        payload.get("pre_cas_operation_id"),
        pattern=_ACCEPTANCE_ID_RE,
        reason_code="PRE_CAS_ACCEPTANCE_RECEIPT_INVALID",
    )
    acceptance_sha = _sha256(
        payload.get("acceptance_sha256"),
        reason_code="PRE_CAS_ACCEPTANCE_RECEIPT_INVALID",
    )
    readback_sha = _sha256(
        payload.get("readback_acceptance_sha256"),
        reason_code="PRE_CAS_ACCEPTANCE_RECEIPT_INVALID",
    )
    accepted_at = _timestamp(
        payload.get("accepted_at"),
        reason_code="PRE_CAS_ACCEPTANCE_RECEIPT_INVALID",
    )
    issued_at = _timestamp(
        payload.get("issued_at"),
        reason_code="PRE_CAS_ACCEPTANCE_RECEIPT_INVALID",
    )
    if (
        operation_id != acceptance.pre_cas_operation_id
        or acceptance_sha != hashlib.sha256(acceptance.canonical_acceptance).hexdigest()
        or readback_sha != acceptance_sha
        or accepted_at != acceptance.accepted_at
        or issued_at < accepted_at
        or issued_at > now + timedelta(seconds=config.maximum_future_skew_seconds)
    ):
        _fail("PRE_CAS_ACCEPTANCE_READBACK_MISMATCH")
    return _ReceiptFacts(
        signed_receipt=raw,
        receipt_sha256=hashlib.sha256(raw).hexdigest(),
        pre_cas_operation_id=operation_id,
        acceptance_sha256=acceptance_sha,
        append_sequence=_positive_int(
            payload.get("append_sequence"),
            maximum=2**63 - 1,
            reason_code="PRE_CAS_ACCEPTANCE_RECEIPT_INVALID",
        ),
        accepted_at=accepted_at,
        issued_at=issued_at,
    )


def _mint(
    *,
    acceptance: _AcceptanceFacts,
    receipt: _ReceiptFacts,
    config: _ConfigFacts,
) -> VerifiedPhysicalBlobPreCasAcceptance:
    result = VerifiedPhysicalBlobPreCasAcceptance(
        canonical_acceptance=acceptance.canonical_acceptance,
        signed_authority_receipt=receipt.signed_receipt,
        authority_public_key=config.authority_public_key,
        pre_cas_operation_id=acceptance.pre_cas_operation_id,
        source_site=acceptance.source_site,
        destination_site=acceptance.destination_site,
        campaign_id=acceptance.campaign_id,
        release_sha=acceptance.release_sha,
        stream_generation_id=acceptance.stream_generation_id,
        baseline_generation_id=acceptance.baseline_generation_id,
        baseline_manifest_sha256=acceptance.baseline_manifest_sha256,
        baseline_wal_lsn=acceptance.baseline_wal_lsn,
        destination_age_recipient=acceptance.destination_age_recipient,
        former_writer_epoch=acceptance.former_writer_epoch,
        former_writer_lease_id=acceptance.former_writer_lease_id,
        former_witness_transition_id=acceptance.former_witness_transition_id,
        former_witnessed_term_proof_sha256=acceptance.former_witnessed_term_proof_sha256,
        source_evidence_schema=acceptance.source_evidence_schema,
        source_evidence_sha256=acceptance.source_evidence_sha256,
        blob_timeline_id=acceptance.blob_timeline_id,
        blob_route_binding_sha256=acceptance.blob_route_binding_sha256,
        blob_mapping_plaintext_sha256=acceptance.blob_mapping_plaintext_sha256,
        blob_mapping_receipt_sha256=acceptance.blob_mapping_receipt_sha256,
        blob_mapping_object_key=acceptance.blob_mapping_object_key,
        blob_mapping_object_version_id=acceptance.blob_mapping_object_version_id,
        blob_mapping_ciphertext_sha256=acceptance.blob_mapping_ciphertext_sha256,
        blob_mapping_ciphertext_bytes=acceptance.blob_mapping_ciphertext_bytes,
        original_v1_inventory_receipt_sha256=acceptance.original_v1_inventory_receipt_sha256,
        blob_receipts_sha256=acceptance.blob_receipts_sha256,
        blob_entry_count=acceptance.blob_entry_count,
        blob_mapping_eligible_replay_wal_lsn=acceptance.blob_mapping_eligible_replay_wal_lsn,
        accepted_at=acceptance.accepted_at,
        authority_receipt_sha256=receipt.receipt_sha256,
        authority_append_sequence=receipt.append_sequence,
        authority_issued_at=receipt.issued_at,
    )
    object.__setattr__(result, "_capability", _VERIFIED_ACCEPTANCE_CAPABILITY)
    return result


def verify_physical_blob_pre_cas_acceptance(
    *,
    canonical_acceptance: bytes,
    signed_authority_receipt: bytes,
    config: PhysicalBlobPreCasAcceptanceConfig,
    now: datetime,
) -> VerifiedPhysicalBlobPreCasAcceptance:
    """Verify an externally read durable record without touching its authority."""

    config_facts = _config(config)
    observed_at = _utc(now, reason_code="PRE_CAS_ACCEPTANCE_CLOCK_INVALID")
    acceptance = _acceptance_facts(
        canonical_acceptance,
        config=config_facts,
        now=observed_at,
    )
    receipt = _receipt_facts(
        signed_authority_receipt,
        acceptance=acceptance,
        config=config_facts,
        now=observed_at,
    )
    return _mint(acceptance=acceptance, receipt=receipt, config=config_facts)


def require_verified_physical_blob_pre_cas_acceptance(
    value: object,
    *,
    config: PhysicalBlobPreCasAcceptanceConfig,
    now: datetime,
) -> VerifiedPhysicalBlobPreCasAcceptance:
    """Reverify only the durable signed record; no former-source check occurs."""

    if (
        type(value) is not VerifiedPhysicalBlobPreCasAcceptance
        or value._capability is not _VERIFIED_ACCEPTANCE_CAPABILITY
    ):
        _fail("PRE_CAS_ACCEPTANCE_CAPABILITY_REQUIRED")
    # Dataclass fields remain mutable through hostile in-process reflection.
    # Validate integer wrappers before tuple equality because ``True == 1``.
    _positive_int(
        value.former_writer_epoch,
        maximum=2**63 - 1,
        reason_code="PRE_CAS_ACCEPTANCE_CAPABILITY_TAMPERED",
    )
    _positive_int(
        value.blob_timeline_id,
        maximum=0xFFFFFFFF,
        reason_code="PRE_CAS_ACCEPTANCE_CAPABILITY_TAMPERED",
    )
    _positive_int(
        value.blob_mapping_ciphertext_bytes,
        maximum=100 * 1024 * 1024,
        reason_code="PRE_CAS_ACCEPTANCE_CAPABILITY_TAMPERED",
    )
    _positive_int(
        value.blob_entry_count,
        maximum=16_384,
        reason_code="PRE_CAS_ACCEPTANCE_CAPABILITY_TAMPERED",
    )
    _positive_int(
        value.authority_append_sequence,
        maximum=2**63 - 1,
        reason_code="PRE_CAS_ACCEPTANCE_CAPABILITY_TAMPERED",
    )
    verified = verify_physical_blob_pre_cas_acceptance(
        canonical_acceptance=value.canonical_acceptance,
        signed_authority_receipt=value.signed_authority_receipt,
        config=config,
        now=now,
    )
    expected = (
        verified.canonical_acceptance,
        verified.signed_authority_receipt,
        verified.authority_public_key,
        verified.pre_cas_operation_id,
        verified.source_site,
        verified.destination_site,
        verified.campaign_id,
        verified.release_sha,
        verified.stream_generation_id,
        verified.baseline_generation_id,
        verified.baseline_manifest_sha256,
        verified.baseline_wal_lsn,
        verified.destination_age_recipient,
        verified.former_writer_epoch,
        verified.former_writer_lease_id,
        verified.former_witness_transition_id,
        verified.former_witnessed_term_proof_sha256,
        verified.source_evidence_schema,
        verified.source_evidence_sha256,
        verified.blob_timeline_id,
        verified.blob_route_binding_sha256,
        verified.blob_mapping_plaintext_sha256,
        verified.blob_mapping_receipt_sha256,
        verified.blob_mapping_object_key,
        verified.blob_mapping_object_version_id,
        verified.blob_mapping_ciphertext_sha256,
        verified.blob_mapping_ciphertext_bytes,
        verified.original_v1_inventory_receipt_sha256,
        verified.blob_receipts_sha256,
        verified.blob_entry_count,
        verified.blob_mapping_eligible_replay_wal_lsn,
        verified.accepted_at,
        verified.authority_receipt_sha256,
        verified.authority_append_sequence,
        verified.authority_issued_at,
    )
    actual = (
        value.canonical_acceptance,
        value.signed_authority_receipt,
        value.authority_public_key,
        value.pre_cas_operation_id,
        value.source_site,
        value.destination_site,
        value.campaign_id,
        value.release_sha,
        value.stream_generation_id,
        value.baseline_generation_id,
        value.baseline_manifest_sha256,
        value.baseline_wal_lsn,
        value.destination_age_recipient,
        value.former_writer_epoch,
        value.former_writer_lease_id,
        value.former_witness_transition_id,
        value.former_witnessed_term_proof_sha256,
        value.source_evidence_schema,
        value.source_evidence_sha256,
        value.blob_timeline_id,
        value.blob_route_binding_sha256,
        value.blob_mapping_plaintext_sha256,
        value.blob_mapping_receipt_sha256,
        value.blob_mapping_object_key,
        value.blob_mapping_object_version_id,
        value.blob_mapping_ciphertext_sha256,
        value.blob_mapping_ciphertext_bytes,
        value.original_v1_inventory_receipt_sha256,
        value.blob_receipts_sha256,
        value.blob_entry_count,
        value.blob_mapping_eligible_replay_wal_lsn,
        value.accepted_at,
        value.authority_receipt_sha256,
        value.authority_append_sequence,
        value.authority_issued_at,
    )
    if actual != expected:
        _fail("PRE_CAS_ACCEPTANCE_CAPABILITY_TAMPERED")
    return value


def persist_physical_blob_pre_cas_acceptance(
    *,
    config: PhysicalBlobPreCasAcceptanceConfig,
    prior_activation: VerifiedObjectDeltaRoleMatrixActivation,
    former_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    verified_physical_wal_evidence: VerifiedPhysicalWalPromotionEvidence,
    verified_v2_blob_requirement: VerifiedPhysicalWalPromotionV2BlobRequirement,
    blob_evidence_config: PhysicalBlobReceiverPromotionEvidenceConfig,
    verified_blob_binding: VerifiedPhysicalBlobObjectStorageBinding,
    pre_cas_operation_id: str,
    authority: PhysicalBlobPreCasAcceptanceAuthority,
    now: datetime,
) -> VerifiedPhysicalBlobPreCasAcceptance:
    """Validate live predecessor evidence, then invoke one durable readback boundary."""

    config_facts = _config(config)
    observed_at = _utc(now, reason_code="PRE_CAS_ACCEPTANCE_CLOCK_INVALID")
    prior = _prior_facts(
        prior_activation=prior_activation,
        former_witnessed_term=former_witnessed_term,
        now=observed_at,
    )
    source = _wal_source_facts(verified_physical_wal_evidence)
    blob = _v2_facts(
        verified_v2_blob_requirement,
        blob_evidence_config=blob_evidence_config,
        verified_blob_binding=verified_blob_binding,
        now=observed_at,
    )
    _cross_bind_pre_cas(prior=prior, source=source, blob=blob)
    payload = _acceptance_payload(
        pre_cas_operation_id=pre_cas_operation_id,
        prior=prior,
        source=source,
        blob=blob,
        accepted_at=observed_at,
    )
    try:
        canonical_acceptance = canonical_json_bytes(payload)
    except (TypeError, ValueError):  # pragma: no cover - all payload inputs were normalized.
        _fail("PRE_CAS_ACCEPTANCE_SERIALIZATION_INVALID")
    append_and_read_back = getattr(authority, "append_and_read_back", None)
    if not callable(append_and_read_back):
        _fail("DURABLE_ACCEPTANCE_AUTHORITY_MISSING")
    try:
        signed_receipt = append_and_read_back(
            canonical_acceptance=canonical_acceptance,
            acceptance_sha256=hashlib.sha256(canonical_acceptance).hexdigest(),
        )
    except Exception:
        _fail("DURABLE_ACCEPTANCE_APPEND_OR_READBACK_FAILED")
    if not isinstance(signed_receipt, bytes):
        _fail("DURABLE_ACCEPTANCE_READBACK_TYPE_INVALID")
    return verify_physical_blob_pre_cas_acceptance(
        canonical_acceptance=canonical_acceptance,
        signed_authority_receipt=signed_receipt,
        config=config,
        now=observed_at,
    )
