"""Pure, fail-closed composition contract for a physical FI↔IR promotion.

This module deliberately stops *before* any live action.  It rechecks opaque
physical-WAL, remote-ack, role-activation, Witness-term, and durable pre-CAS
Blob-acceptance capabilities, then binds their common FI/IR route, baseline,
and former/new Writer-Witness identities into one short-lived local
preparation capability.

No function here opens a filesystem path, connects to PostgreSQL, Object
Storage, a peer, Docker, SSH, a Witness, or a traffic system.  In particular,
``live`` below means that a previously verified signed Witness proof is still
within its local validity window; it is *not* a live Witness query and it does
not consume a Witness term.  A prepared result is therefore never permission
to start a writer.

The v2 Blob requirement is mandatory at the *pre-CAS* durable acceptance
boundary.  This post-CAS coordinator accepts only that opaque,
authority-signed acceptance, never a raw/v1 Blob receipt and never a renewed
former-source liveness check.  The acceptance and WAL evidence must agree on
every shared source, destination, release, baseline, and former
Writer-Witness field, including the destination age recipient and exact
source-evidence hash.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    OBJECT_KEY_RE,
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
from core.physical_blob_pre_cas_acceptance import (
    PhysicalBlobPreCasAcceptanceConfig,
    PhysicalBlobPreCasAcceptanceError,
    VerifiedPhysicalBlobPreCasAcceptance,
    require_verified_physical_blob_pre_cas_acceptance,
)
from core.physical_wal_promotion_gate import (
    PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA,
    PhysicalWalPromotionAssessment,
    PhysicalWalPromotionGateError,
    VerifiedPhysicalWalPromotionEvidence,
    assess_physical_wal_promotion,
    require_physical_wal_promotion_eligible,
    require_verified_physical_wal_promotion_evidence,
)
from core.physical_wal_remote_ack import (
    PhysicalWalRemoteAckError,
    VerifiedPhysicalWalRemoteAckEvidence,
    require_verified_physical_wal_remote_ack_evidence,
)


__all__ = (
    "PHYSICAL_POSTGRES_PROMOTION_COORDINATOR_DEFAULT_ENABLED",
    "PHYSICAL_POSTGRES_PROMOTION_COORDINATOR_SCHEMA",
    "PhysicalPromotionDatabaseTransactionAdapter",
    "PhysicalPromotionFormerWriterFenceAdapter",
    "PhysicalPromotionTargetRecoveryAdapter",
    "PhysicalPromotionTrafficFenceAdapter",
    "PhysicalPromotionWitnessCasAdapter",
    "PhysicalPostgresPromotionCoordinatorConfig",
    "PhysicalPostgresPromotionCoordinatorError",
    "PhysicalPostgresPromotionRuntimeAdapters",
    "PreparedPhysicalPostgresPromotion",
    "PreparedPhysicalPostgresPromotionExecutionBoundary",
    "prepare_physical_postgres_promotion",
    "prepare_physical_postgres_promotion_execution_boundary",
    "require_prepared_physical_postgres_promotion",
    "require_prepared_physical_postgres_promotion_execution_boundary",
)


PHYSICAL_POSTGRES_PROMOTION_COORDINATOR_SCHEMA = (
    "gold-trade-physical-postgres-promotion-coordinator-v1"
)
PHYSICAL_POSTGRES_PROMOTION_COORDINATOR_DEFAULT_ENABLED = False

_PREPARED_PROMOTION_CAPABILITY = object()
_PREPARED_EXECUTION_BOUNDARY_CAPABILITY = object()
_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$"
)
_WITNESS_TRANSITION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class PhysicalPostgresPromotionCoordinatorError(ValueError):
    """The supplied local promotion proof is incomplete or unsafe."""


@dataclass(frozen=True)
class PhysicalPostgresPromotionCoordinatorConfig:
    """Explicit default-off switch for this pure preparation contract."""

    enabled: bool = PHYSICAL_POSTGRES_PROMOTION_COORDINATOR_DEFAULT_ENABLED


class PhysicalPromotionWitnessCasAdapter(Protocol):
    """Future runtime interface; this module never calls it."""

    def consume_promotion_term(self, *, prepared: "PreparedPhysicalPostgresPromotion") -> object:
        """Durably compare-and-swap/consume the witnessed promotion term."""


class PhysicalPromotionFormerWriterFenceAdapter(Protocol):
    """Future runtime interface; this module never calls it."""

    def fence_former_writer(self, *, prepared: "PreparedPhysicalPostgresPromotion") -> object:
        """Prevent the former source from accepting writes."""


class PhysicalPromotionTargetRecoveryAdapter(Protocol):
    """Future runtime interface; this module never calls it."""

    def recover_and_promote_target(
        self, *, prepared: "PreparedPhysicalPostgresPromotion"
    ) -> object:
        """Restore/replay the target and promote it only after fencing."""


class PhysicalPromotionTrafficFenceAdapter(Protocol):
    """Future runtime interface; this module never calls it."""

    def switch_fenced_traffic(self, *, prepared: "PreparedPhysicalPostgresPromotion") -> object:
        """Move traffic only through a witnessed, fenced route transition."""


class PhysicalPromotionDatabaseTransactionAdapter(Protocol):
    """Future runtime interface; this module never calls it."""

    def run_promotion_transaction(self, *, prepared: "PreparedPhysicalPostgresPromotion") -> object:
        """Record the promotion atomically with application continuity work."""


@dataclass(frozen=True)
class PhysicalPostgresPromotionRuntimeAdapters:
    """Explicit runtime dependencies required by the non-executing boundary.

    All defaults are ``None`` deliberately.  Constructing this object does
    not supply an implementation and cannot turn a prepared proof into an
    execution permit.
    """

    witness_cas: PhysicalPromotionWitnessCasAdapter | None = None
    former_writer_fence: PhysicalPromotionFormerWriterFenceAdapter | None = None
    target_recovery: PhysicalPromotionTargetRecoveryAdapter | None = None
    traffic_fence: PhysicalPromotionTrafficFenceAdapter | None = None
    promotion_database_transaction: PhysicalPromotionDatabaseTransactionAdapter | None = None


@dataclass(frozen=True)
class PreparedPhysicalPostgresPromotion:
    """Opaque, local-only preparation for one FI↔IR promotion direction.

    The retained opaque inputs are revalidated by the corresponding
    ``require_*`` function.  They are evidence inputs, not handles for a
    network, database, object store, Witness, or process.
    """

    schema: str
    prepared_at: datetime
    source_site: str
    target_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    acknowledged_durable_wal_lsn: str
    receiver_replay_wal_lsn: str
    physical_wal_blob_frontier_wal_lsn: str
    source_writer_epoch: int
    source_writer_lease_id: str
    source_witnessed_term_proof_sha256: str
    candidate_writer_epoch: int
    candidate_writer_lease_id: str
    candidate_witness_transition_id: str
    candidate_witnessed_term_proof_sha256: str
    destination_age_recipient: str
    blob_timeline_id: int
    blob_mapping_receipt_sha256: str
    blob_mapping_object_key: str
    blob_mapping_object_version_id: str
    blob_mapping_ciphertext_sha256: str
    blob_mapping_eligible_replay_wal_lsn: str
    blob_route_binding_sha256: str
    coordinator_config: PhysicalPostgresPromotionCoordinatorConfig
    prior_activation: VerifiedObjectDeltaRoleMatrixActivation
    current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    supplied_physical_wal_eligibility: PhysicalWalPromotionAssessment
    verified_physical_wal_evidence: VerifiedPhysicalWalPromotionEvidence
    verified_remote_ack: VerifiedPhysicalWalRemoteAckEvidence
    verified_pre_cas_blob_acceptance: VerifiedPhysicalBlobPreCasAcceptance
    pre_cas_acceptance_config: PhysicalBlobPreCasAcceptanceConfig
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PreparedPhysicalPostgresPromotionExecutionBoundary:
    """A checked list of required runtime interfaces, not an executor.

    No adapter method is invoked here or by the corresponding require helper.
    The value only makes omitted runtime responsibilities visible and
    fail-closed before a future root-only orchestration implementation exists.
    """

    prepared_promotion: PreparedPhysicalPostgresPromotion
    runtime_adapters: PhysicalPostgresPromotionRuntimeAdapters
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _PriorFacts:
    activation: VerifiedObjectDeltaRoleMatrixActivation
    source_site: str
    target_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    prior_writer_epoch: int
    prior_writer_lease_id: str
    prior_witness_transition_id: str
    prior_witnessed_term_proof_sha256: str
    destination_age_recipient: str
    historical_writer_lease_ids: frozenset[str]
    historical_witness_transition_ids: frozenset[str]


@dataclass(frozen=True)
class _CurrentTermFacts:
    term: VerifiedObjectDeltaRoleMatrixWitnessedTerm
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    proof_sha256: str
    issued_at: datetime


@dataclass(frozen=True)
class _AssessmentFacts:
    assessment: PhysicalWalPromotionAssessment
    source_site: str
    target_site: str
    baseline_generation_id: str
    acknowledged_durable_wal_lsn: str
    receiver_replay_wal_lsn: str
    blob_frontier_wal_lsn: str


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
    prior_holder_site: str
    prior_writer_epoch: int
    prior_writer_lease_id: str
    prior_term_proof_sha256: str
    source_evidence_schema: str
    source_evidence_sha256: str


@dataclass(frozen=True)
class _PreCasBlobFacts:
    acceptance: VerifiedPhysicalBlobPreCasAcceptance
    source_site: str
    target_site: str
    campaign_id: str
    release_sha: str
    stream_generation_id: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    source_writer_epoch: int
    source_writer_lease_id: str
    source_witness_transition_id: str
    source_witnessed_term_proof_sha256: str
    destination_age_recipient: str
    timeline_id: int
    mapping_receipt_sha256: str
    mapping_object_key: str
    mapping_object_version_id: str
    mapping_ciphertext_sha256: str
    mapping_eligible_replay_wal_lsn: str
    route_binding_sha256: str
    source_evidence_schema: str
    source_evidence_sha256: str
    accepted_at: datetime
    authority_issued_at: datetime


def _fail(reason_code: str) -> None:
    raise PhysicalPostgresPromotionCoordinatorError(reason_code)


def _utc(value: object, *, reason_code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(reason_code)
    return value.astimezone(timezone.utc)


def _site(value: object, *, reason_code: str) -> str:
    if not isinstance(value, str) or value not in WEBAPP_SITES:
        _fail(reason_code)
    return value


def _identifier(value: object, *, pattern: object, reason_code: str) -> str:
    if not isinstance(value, str) or not getattr(pattern, "fullmatch")(value):
        _fail(reason_code)
    return value


def _sha256(value: object, *, reason_code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(reason_code)
    return value


def _positive_int(value: object, *, reason_code: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail(reason_code)
    return value


def _lsn(value: object, *, reason_code: str) -> tuple[str, int]:
    if not isinstance(value, str) or _LSN_RE.fullmatch(value) is None:
        _fail(reason_code)
    high, low = value.split("/", 1)
    return value, (int(high, 16) << 32) | int(low, 16)


def _config(value: object) -> PhysicalPostgresPromotionCoordinatorConfig:
    if type(value) is not PhysicalPostgresPromotionCoordinatorConfig:
        _fail("COORDINATOR_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("COORDINATOR_DISABLED")
    return value


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            _fail("PHYSICAL_WAL_EVIDENCE_UNVERIFIED")
        result[key] = item
    return result


def _prior_facts(value: object, *, now: datetime) -> _PriorFacts:
    try:
        activation = require_verified_object_delta_role_matrix_activation(value, now=now)
        matrix = require_verified_object_delta_role_matrix(activation._matrix)
        prior_term = require_verified_object_delta_role_matrix_witnessed_term(
            activation._witnessed_term,
            now=now,
        )
        active_route = active_object_delta_role_matrix_route(matrix)
        source_binding = active_route.source_pin.binding
        source_role = object_delta_role_matrix_site_role(
            matrix,
            site=source_binding.source_site,
        )
        target_role = object_delta_role_matrix_site_role(
            matrix,
            site=source_binding.destination_site,
        )
        history = activation._history
    except (AttributeError, ObjectDeltaRoleMatrixError, ObjectDeltaRoleMatrixRolloverError):
        _fail("PRIOR_ROLE_ACTIVATION_UNVERIFIED")

    source_site = _site(source_binding.source_site, reason_code="PRIOR_ROLE_ACTIVATION_UNVERIFIED")
    target_site = _site(
        source_binding.destination_site,
        reason_code="PRIOR_ROLE_ACTIVATION_UNVERIFIED",
    )
    if source_site == target_site:
        _fail("PRIOR_ROLE_ACTIVATION_UNVERIFIED")
    if (
        source_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE
        or target_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER
    ):
        _fail("PRIOR_ROLE_DIRECTION_INVALID")
    if prior_term.holder_site != source_site:
        _fail("PRIOR_ROLE_TERM_HOLDER_MISMATCH")
    if type(history) is not tuple:
        _fail("PRIOR_ROLE_ACTIVATION_UNVERIFIED")
    lease_ids: set[str] = set()
    transition_ids: set[str] = set()
    for record in history:
        lease_ids.add(
            _identifier(
                record.writer_lease_id,
                pattern=LEASE_ID_RE,
                reason_code="PRIOR_ROLE_ACTIVATION_UNVERIFIED",
            )
        )
        transition_ids.add(
            _identifier(
                record.witness_transition_id,
                pattern=_WITNESS_TRANSITION_ID_RE,
                reason_code="PRIOR_ROLE_ACTIVATION_UNVERIFIED",
            )
        )
    policy = active_route.source_pin.transport_policy
    recipient = (
        policy.webapp_fi_age_recipient
        if target_site == "webapp_fi"
        else policy.webapp_ir_age_recipient
    )
    if not isinstance(recipient, str) or not recipient:
        _fail("PRIOR_ROLE_DESTINATION_RECIPIENT_INVALID")
    return _PriorFacts(
        activation=activation,
        source_site=source_site,
        target_site=target_site,
        campaign_id=_identifier(
            source_binding.campaign_id,
            pattern=CAMPAIGN_ID_RE,
            reason_code="PRIOR_ROLE_ACTIVATION_UNVERIFIED",
        ),
        release_sha=_identifier(
            source_binding.release_sha,
            pattern=RELEASE_SHA_RE,
            reason_code="PRIOR_ROLE_ACTIVATION_UNVERIFIED",
        ),
        stream_generation_id=_identifier(
            source_binding.stream_generation_id,
            pattern=STREAM_GENERATION_ID_RE,
            reason_code="PRIOR_ROLE_ACTIVATION_UNVERIFIED",
        ),
        prior_writer_epoch=_positive_int(
            prior_term.writer_epoch,
            reason_code="PRIOR_ROLE_TERM_EPOCH_INVALID",
            maximum=2**63 - 1,
        ),
        prior_writer_lease_id=_identifier(
            prior_term.writer_lease_id,
            pattern=LEASE_ID_RE,
            reason_code="PRIOR_ROLE_TERM_LEASE_INVALID",
        ),
        prior_witness_transition_id=_identifier(
            prior_term.witness_transition_id,
            pattern=_WITNESS_TRANSITION_ID_RE,
            reason_code="PRIOR_ROLE_TERM_TRANSITION_INVALID",
        ),
        prior_witnessed_term_proof_sha256=_sha256(
            prior_term.proof_sha256,
            reason_code="PRIOR_ROLE_TERM_PROOF_INVALID",
        ),
        destination_age_recipient=recipient,
        historical_writer_lease_ids=frozenset(lease_ids),
        historical_witness_transition_ids=frozenset(transition_ids),
    )


def _current_term_facts(
    value: object,
    *,
    prior: _PriorFacts,
    now: datetime,
) -> _CurrentTermFacts:
    try:
        term = require_live_object_delta_role_matrix_witnessed_term(value, now=now)
    except ObjectDeltaRoleMatrixRolloverError:
        _fail("CURRENT_WITNESS_TERM_UNVERIFIED")
    holder_site = _site(term.holder_site, reason_code="CURRENT_WITNESS_TERM_UNVERIFIED")
    epoch = _positive_int(
        term.writer_epoch,
        reason_code="CURRENT_WITNESS_TERM_EPOCH_INVALID",
        maximum=2**63 - 1,
    )
    lease = _identifier(
        term.writer_lease_id,
        pattern=LEASE_ID_RE,
        reason_code="CURRENT_WITNESS_TERM_LEASE_INVALID",
    )
    transition = _identifier(
        term.witness_transition_id,
        pattern=_WITNESS_TRANSITION_ID_RE,
        reason_code="CURRENT_WITNESS_TERM_TRANSITION_INVALID",
    )
    proof_sha256 = _sha256(
        term.proof_sha256,
        reason_code="CURRENT_WITNESS_TERM_PROOF_INVALID",
    )
    issued_at = _utc(
        term.issued_at,
        reason_code="CURRENT_WITNESS_TERM_ISSUED_AT_INVALID",
    )
    if holder_site != prior.target_site:
        _fail("CURRENT_WITNESS_TERM_WRONG_TARGET")
    if epoch <= prior.prior_writer_epoch:
        _fail("CURRENT_WITNESS_TERM_NOT_STRICTLY_NEWER")
    if lease in prior.historical_writer_lease_ids:
        _fail("CURRENT_WITNESS_TERM_REUSES_HISTORICAL_LEASE")
    if transition in prior.historical_witness_transition_ids:
        _fail("CURRENT_WITNESS_TERM_REUSES_HISTORICAL_TRANSITION")
    if proof_sha256 == prior.prior_witnessed_term_proof_sha256:
        _fail("CURRENT_WITNESS_TERM_REUSES_PRIOR_PROOF")
    return _CurrentTermFacts(
        term=term,
        holder_site=holder_site,
        writer_epoch=epoch,
        writer_lease_id=lease,
        witness_transition_id=transition,
        proof_sha256=proof_sha256,
        issued_at=issued_at,
    )


def _assessment_facts(value: object, *, reason_code: str) -> _AssessmentFacts:
    if type(value) is not PhysicalWalPromotionAssessment:
        _fail(reason_code)
    if value.status != "eligible" or type(value.reason_codes) is not tuple or value.reason_codes:
        _fail(reason_code)
    source_site = _site(value.source_site, reason_code=reason_code)
    target_site = _site(value.target_site, reason_code=reason_code)
    if source_site == target_site:
        _fail(reason_code)
    baseline = _identifier(
        value.baseline_generation_id,
        pattern=STREAM_GENERATION_ID_RE,
        reason_code=reason_code,
    )
    acknowledged, _ = _lsn(value.acknowledged_durable_wal_lsn, reason_code=reason_code)
    receiver, receiver_value = _lsn(value.receiver_replay_wal_lsn, reason_code=reason_code)
    blob, blob_value = _lsn(value.blob_object_frontier_wal_lsn, reason_code=reason_code)
    _acknowledged, acknowledged_value = _lsn(
        value.acknowledged_durable_wal_lsn,
        reason_code=reason_code,
    )
    if receiver_value < acknowledged_value or blob_value < acknowledged_value:
        _fail(reason_code)
    return _AssessmentFacts(
        assessment=value,
        source_site=source_site,
        target_site=target_site,
        baseline_generation_id=baseline,
        acknowledged_durable_wal_lsn=acknowledged,
        receiver_replay_wal_lsn=receiver,
        blob_frontier_wal_lsn=blob,
    )


def _assessment_projection(value: _AssessmentFacts) -> tuple[str, str, str, str, str, str]:
    return (
        value.source_site,
        value.target_site,
        value.baseline_generation_id,
        value.acknowledged_durable_wal_lsn,
        value.receiver_replay_wal_lsn,
        value.blob_frontier_wal_lsn,
    )


def _wal_source_facts(value: VerifiedPhysicalWalPromotionEvidence) -> _WalSourceFacts:
    raw = value.source_durability_receipt
    if not isinstance(raw, bytes) or not raw:
        _fail("PHYSICAL_WAL_EVIDENCE_UNVERIFIED")
    try:
        payload = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("PHYSICAL_WAL_EVIDENCE_UNVERIFIED")
    if not isinstance(payload, dict):
        _fail("PHYSICAL_WAL_EVIDENCE_UNVERIFIED")
    if payload.get("schema") != PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA:
        _fail("PHYSICAL_WAL_EVIDENCE_UNVERIFIED")
    try:
        if canonical_json_bytes(payload) != raw:
            _fail("PHYSICAL_WAL_EVIDENCE_UNVERIFIED")
    except (TypeError, ValueError):
        _fail("PHYSICAL_WAL_EVIDENCE_UNVERIFIED")
    source_site = _site(payload.get("source_site"), reason_code="PHYSICAL_WAL_EVIDENCE_UNVERIFIED")
    target_site = _site(
        payload.get("destination_site"),
        reason_code="PHYSICAL_WAL_EVIDENCE_UNVERIFIED",
    )
    if source_site == target_site:
        _fail("PHYSICAL_WAL_EVIDENCE_UNVERIFIED")
    return _WalSourceFacts(
        source_site=source_site,
        destination_site=target_site,
        campaign_id=_identifier(
            payload.get("campaign_id"),
            pattern=CAMPAIGN_ID_RE,
            reason_code="PHYSICAL_WAL_EVIDENCE_UNVERIFIED",
        ),
        release_sha=_identifier(
            payload.get("release_sha"),
            pattern=RELEASE_SHA_RE,
            reason_code="PHYSICAL_WAL_EVIDENCE_UNVERIFIED",
        ),
        stream_generation_id=_identifier(
            payload.get("stream_generation_id"),
            pattern=STREAM_GENERATION_ID_RE,
            reason_code="PHYSICAL_WAL_EVIDENCE_UNVERIFIED",
        ),
        baseline_generation_id=_identifier(
            payload.get("baseline_generation_id"),
            pattern=STREAM_GENERATION_ID_RE,
            reason_code="PHYSICAL_WAL_EVIDENCE_UNVERIFIED",
        ),
        baseline_manifest_sha256=_sha256(
            payload.get("baseline_manifest_sha256"),
            reason_code="PHYSICAL_WAL_EVIDENCE_UNVERIFIED",
        ),
        baseline_wal_lsn=_lsn(
            payload.get("baseline_wal_lsn"),
            reason_code="PHYSICAL_WAL_EVIDENCE_UNVERIFIED",
        )[0],
        prior_holder_site=_site(
            payload.get("prior_holder_site"),
            reason_code="PHYSICAL_WAL_EVIDENCE_UNVERIFIED",
        ),
        prior_writer_epoch=_positive_int(
            payload.get("prior_writer_epoch"),
            reason_code="PHYSICAL_WAL_EVIDENCE_UNVERIFIED",
            maximum=2**63 - 1,
        ),
        prior_writer_lease_id=_identifier(
            payload.get("prior_writer_lease_id"),
            pattern=LEASE_ID_RE,
            reason_code="PHYSICAL_WAL_EVIDENCE_UNVERIFIED",
        ),
        prior_term_proof_sha256=_sha256(
            payload.get("prior_term_proof_sha256"),
            reason_code="PHYSICAL_WAL_EVIDENCE_UNVERIFIED",
        ),
        source_evidence_schema=PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA,
        source_evidence_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _pre_cas_blob_facts(
    value: object,
    *,
    config: PhysicalBlobPreCasAcceptanceConfig,
    now: datetime,
) -> _PreCasBlobFacts:
    """Read the signed pre-CAS record without touching a former source."""

    try:
        acceptance = require_verified_physical_blob_pre_cas_acceptance(
            value,
            config=config,
            now=now,
        )
    except PhysicalBlobPreCasAcceptanceError:
        _fail("PRE_CAS_BLOB_ACCEPTANCE_UNVERIFIED")
    if type(acceptance) is not VerifiedPhysicalBlobPreCasAcceptance:
        _fail("PRE_CAS_BLOB_ACCEPTANCE_UNVERIFIED")
    baseline_wal_lsn, _ = _lsn(
        acceptance.baseline_wal_lsn,
        reason_code="PRE_CAS_BLOB_ACCEPTANCE_BASELINE_INVALID",
    )
    mapping_replay_lsn, _ = _lsn(
        acceptance.blob_mapping_eligible_replay_wal_lsn,
        reason_code="PRE_CAS_BLOB_ACCEPTANCE_INVENTORY_INVALID",
    )
    if mapping_replay_lsn != baseline_wal_lsn:
        _fail("PRE_CAS_BLOB_ACCEPTANCE_REPLAY_SCOPE_MISMATCH")
    return _PreCasBlobFacts(
        acceptance=acceptance,
        source_site=_site(acceptance.source_site, reason_code="PRE_CAS_BLOB_ACCEPTANCE_ROUTE_INVALID"),
        target_site=_site(acceptance.destination_site, reason_code="PRE_CAS_BLOB_ACCEPTANCE_ROUTE_INVALID"),
        campaign_id=_identifier(
            acceptance.campaign_id,
            pattern=CAMPAIGN_ID_RE,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_ROUTE_INVALID",
        ),
        release_sha=_identifier(
            acceptance.release_sha,
            pattern=RELEASE_SHA_RE,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_ROUTE_INVALID",
        ),
        stream_generation_id=_identifier(
            acceptance.stream_generation_id,
            pattern=STREAM_GENERATION_ID_RE,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_ROUTE_INVALID",
        ),
        baseline_generation_id=_identifier(
            acceptance.baseline_generation_id,
            pattern=STREAM_GENERATION_ID_RE,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_BASELINE_INVALID",
        ),
        baseline_manifest_sha256=_sha256(
            acceptance.baseline_manifest_sha256,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_BASELINE_INVALID",
        ),
        baseline_wal_lsn=baseline_wal_lsn,
        source_writer_epoch=_positive_int(
            acceptance.former_writer_epoch,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_FORMER_TERM_INVALID",
            maximum=2**63 - 1,
        ),
        source_writer_lease_id=_identifier(
            acceptance.former_writer_lease_id,
            pattern=LEASE_ID_RE,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_FORMER_TERM_INVALID",
        ),
        source_witness_transition_id=_identifier(
            acceptance.former_witness_transition_id,
            pattern=_WITNESS_TRANSITION_ID_RE,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_FORMER_TERM_INVALID",
        ),
        source_witnessed_term_proof_sha256=_sha256(
            acceptance.former_witnessed_term_proof_sha256,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_FORMER_TERM_INVALID",
        ),
        destination_age_recipient=(
            acceptance.destination_age_recipient
            if isinstance(acceptance.destination_age_recipient, str)
            and acceptance.destination_age_recipient
            else _fail("PRE_CAS_BLOB_ACCEPTANCE_ROUTE_INVALID")
        ),
        timeline_id=_positive_int(
            acceptance.blob_timeline_id,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_INVENTORY_INVALID",
            maximum=0xFFFFFFFF,
        ),
        mapping_receipt_sha256=_sha256(
            acceptance.blob_mapping_receipt_sha256,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_INVENTORY_INVALID",
        ),
        mapping_object_key=_identifier(
            acceptance.blob_mapping_object_key,
            pattern=OBJECT_KEY_RE,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_INVENTORY_INVALID",
        ),
        mapping_object_version_id=_identifier(
            acceptance.blob_mapping_object_version_id,
            pattern=VERSION_ID_RE,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_INVENTORY_INVALID",
        ),
        mapping_ciphertext_sha256=_sha256(
            acceptance.blob_mapping_ciphertext_sha256,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_INVENTORY_INVALID",
        ),
        mapping_eligible_replay_wal_lsn=mapping_replay_lsn,
        route_binding_sha256=_sha256(
            acceptance.blob_route_binding_sha256,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_INVENTORY_INVALID",
        ),
        source_evidence_schema=(
            acceptance.source_evidence_schema
            if acceptance.source_evidence_schema
            == PHYSICAL_WAL_SOURCE_DURABILITY_RECEIPT_SCHEMA
            else _fail("PRE_CAS_BLOB_ACCEPTANCE_SOURCE_EVIDENCE_INVALID")
        ),
        source_evidence_sha256=_sha256(
            acceptance.source_evidence_sha256,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_SOURCE_EVIDENCE_INVALID",
        ),
        accepted_at=_utc(
            acceptance.accepted_at,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_TIMESTAMP_INVALID",
        ),
        authority_issued_at=_utc(
            acceptance.authority_issued_at,
            reason_code="PRE_CAS_BLOB_ACCEPTANCE_TIMESTAMP_INVALID",
        ),
    )


def _reassess_physical_wal(
    *,
    supplied_eligibility: object,
    verified_evidence: object,
    verified_remote_ack: object,
    prior_activation: VerifiedObjectDeltaRoleMatrixActivation,
    current_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    now: datetime,
) -> tuple[_AssessmentFacts, _WalSourceFacts]:
    try:
        supplied = require_physical_wal_promotion_eligible(supplied_eligibility)
    except PhysicalWalPromotionGateError:
        _fail("PHYSICAL_WAL_ELIGIBILITY_UNVERIFIED")
    supplied_facts = _assessment_facts(
        supplied,
        reason_code="PHYSICAL_WAL_ELIGIBILITY_UNVERIFIED",
    )
    try:
        evidence = require_verified_physical_wal_promotion_evidence(verified_evidence)
    except PhysicalWalPromotionGateError:
        _fail("PHYSICAL_WAL_EVIDENCE_UNVERIFIED")
    try:
        remote_ack = require_verified_physical_wal_remote_ack_evidence(
            verified_remote_ack,
            now=now,
        )
    except PhysicalWalRemoteAckError:
        _fail("PHYSICAL_WAL_REMOTE_ACK_UNVERIFIED")
    reassessed = assess_physical_wal_promotion(
        prior_activation=prior_activation,
        candidate_witnessed_term=current_term,
        verified_evidence=evidence,
        verified_remote_ack=remote_ack,
        now=now,
    )
    try:
        reassessed = require_physical_wal_promotion_eligible(reassessed)
    except PhysicalWalPromotionGateError:
        _fail("PHYSICAL_WAL_ELIGIBILITY_REASSESSMENT_BLOCKED")
    reassessed_facts = _assessment_facts(
        reassessed,
        reason_code="PHYSICAL_WAL_ELIGIBILITY_REASSESSMENT_BLOCKED",
    )
    if _assessment_projection(supplied_facts) != _assessment_projection(reassessed_facts):
        _fail("PHYSICAL_WAL_ELIGIBILITY_REASSESSMENT_MISMATCH")
    return reassessed_facts, _wal_source_facts(evidence)


def _validate_cross_bindings(
    *,
    prior: _PriorFacts,
    current: _CurrentTermFacts,
    assessment: _AssessmentFacts,
    wal_source: _WalSourceFacts,
    blob: _PreCasBlobFacts,
) -> None:
    if (
        assessment.source_site != prior.source_site
        or assessment.target_site != prior.target_site
    ):
        _fail("PHYSICAL_WAL_ELIGIBILITY_ROUTE_MISMATCH")
    if (
        wal_source.source_site != prior.source_site
        or wal_source.destination_site != prior.target_site
        or wal_source.campaign_id != prior.campaign_id
        or wal_source.release_sha != prior.release_sha
        or wal_source.stream_generation_id != prior.stream_generation_id
    ):
        _fail("PHYSICAL_WAL_EVIDENCE_ROUTE_MISMATCH")
    if (
        wal_source.prior_holder_site != prior.source_site
        or wal_source.prior_writer_epoch != prior.prior_writer_epoch
        or wal_source.prior_writer_lease_id != prior.prior_writer_lease_id
        or wal_source.prior_term_proof_sha256 != prior.prior_witnessed_term_proof_sha256
    ):
        _fail("PHYSICAL_WAL_EVIDENCE_SOURCE_TERM_MISMATCH")
    if assessment.baseline_generation_id != wal_source.baseline_generation_id:
        _fail("PHYSICAL_WAL_ELIGIBILITY_BASELINE_MISMATCH")
    if (
        blob.source_site != prior.source_site
        or blob.target_site != prior.target_site
        or blob.campaign_id != prior.campaign_id
        or blob.release_sha != prior.release_sha
        or blob.stream_generation_id != prior.stream_generation_id
    ):
        _fail("PRE_CAS_BLOB_ACCEPTANCE_ROUTE_MISMATCH")
    if (
        blob.baseline_generation_id != wal_source.baseline_generation_id
        or blob.baseline_manifest_sha256 != wal_source.baseline_manifest_sha256
        or blob.baseline_wal_lsn != wal_source.baseline_wal_lsn
    ):
        _fail("PRE_CAS_BLOB_ACCEPTANCE_BASELINE_MISMATCH")
    if (
        blob.source_writer_epoch != prior.prior_writer_epoch
        or blob.source_writer_lease_id != prior.prior_writer_lease_id
        or blob.source_witness_transition_id != prior.prior_witness_transition_id
        or blob.source_witnessed_term_proof_sha256 != prior.prior_witnessed_term_proof_sha256
    ):
        _fail("PRE_CAS_BLOB_ACCEPTANCE_SOURCE_TERM_MISMATCH")
    if blob.destination_age_recipient != prior.destination_age_recipient:
        _fail("PRE_CAS_BLOB_ACCEPTANCE_DESTINATION_RECIPIENT_MISMATCH")
    if (
        blob.source_evidence_schema != wal_source.source_evidence_schema
        or blob.source_evidence_sha256 != wal_source.source_evidence_sha256
    ):
        _fail("PRE_CAS_BLOB_ACCEPTANCE_SOURCE_EVIDENCE_MISMATCH")
    if (
        blob.accepted_at > current.issued_at
        or blob.authority_issued_at > current.issued_at
    ):
        _fail("PRE_CAS_BLOB_ACCEPTANCE_AFTER_SUCCESSOR_TERM")
    # ``current`` is passed to the physical-WAL gate during reassessment.  The
    # gate verifies its exact proof/epoch/lease against the signed continuity
    # artifact.  Touch all values here so a future refactor cannot accidentally
    # turn this coordinator into a source-term-only check.
    if (
        current.holder_site != prior.target_site
        or current.writer_epoch <= prior.prior_writer_epoch
        or current.writer_lease_id == prior.prior_writer_lease_id
        or current.proof_sha256 == prior.prior_witnessed_term_proof_sha256
    ):
        _fail("CURRENT_WITNESS_TERM_IDENTITY_MISMATCH")


def _prepare(
    *,
    config: object,
    prior_activation: object,
    current_witnessed_term: object,
    supplied_physical_wal_eligibility: object,
    verified_physical_wal_evidence: object,
    verified_remote_ack: object,
    verified_pre_cas_blob_acceptance: object,
    pre_cas_acceptance_config: object,
    now: object,
) -> PreparedPhysicalPostgresPromotion:
    coordinator_config = _config(config)
    observed_at = _utc(now, reason_code="COORDINATOR_CLOCK_INVALID")
    prior = _prior_facts(prior_activation, now=observed_at)
    current = _current_term_facts(
        current_witnessed_term,
        prior=prior,
        now=observed_at,
    )
    assessment, wal_source = _reassess_physical_wal(
        supplied_eligibility=supplied_physical_wal_eligibility,
        verified_evidence=verified_physical_wal_evidence,
        verified_remote_ack=verified_remote_ack,
        prior_activation=prior.activation,
        current_term=current.term,
        now=observed_at,
    )
    blob = _pre_cas_blob_facts(
        verified_pre_cas_blob_acceptance,
        config=pre_cas_acceptance_config,
        now=observed_at,
    )
    _validate_cross_bindings(
        prior=prior,
        current=current,
        assessment=assessment,
        wal_source=wal_source,
        blob=blob,
    )
    result = PreparedPhysicalPostgresPromotion(
        schema=PHYSICAL_POSTGRES_PROMOTION_COORDINATOR_SCHEMA,
        prepared_at=observed_at,
        source_site=prior.source_site,
        target_site=prior.target_site,
        campaign_id=prior.campaign_id,
        release_sha=prior.release_sha,
        stream_generation_id=prior.stream_generation_id,
        baseline_generation_id=wal_source.baseline_generation_id,
        baseline_manifest_sha256=wal_source.baseline_manifest_sha256,
        baseline_wal_lsn=wal_source.baseline_wal_lsn,
        acknowledged_durable_wal_lsn=assessment.acknowledged_durable_wal_lsn,
        receiver_replay_wal_lsn=assessment.receiver_replay_wal_lsn,
        physical_wal_blob_frontier_wal_lsn=assessment.blob_frontier_wal_lsn,
        source_writer_epoch=prior.prior_writer_epoch,
        source_writer_lease_id=prior.prior_writer_lease_id,
        source_witnessed_term_proof_sha256=prior.prior_witnessed_term_proof_sha256,
        candidate_writer_epoch=current.writer_epoch,
        candidate_writer_lease_id=current.writer_lease_id,
        candidate_witness_transition_id=current.witness_transition_id,
        candidate_witnessed_term_proof_sha256=current.proof_sha256,
        destination_age_recipient=prior.destination_age_recipient,
        blob_timeline_id=blob.timeline_id,
        blob_mapping_receipt_sha256=blob.mapping_receipt_sha256,
        blob_mapping_object_key=blob.mapping_object_key,
        blob_mapping_object_version_id=blob.mapping_object_version_id,
        blob_mapping_ciphertext_sha256=blob.mapping_ciphertext_sha256,
        blob_mapping_eligible_replay_wal_lsn=blob.mapping_eligible_replay_wal_lsn,
        blob_route_binding_sha256=blob.route_binding_sha256,
        coordinator_config=coordinator_config,
        prior_activation=prior.activation,
        current_witnessed_term=current.term,
        supplied_physical_wal_eligibility=supplied_physical_wal_eligibility,
        verified_physical_wal_evidence=verified_physical_wal_evidence,
        verified_remote_ack=verified_remote_ack,
        verified_pre_cas_blob_acceptance=verified_pre_cas_blob_acceptance,
        pre_cas_acceptance_config=pre_cas_acceptance_config,
    )
    object.__setattr__(result, "_capability", _PREPARED_PROMOTION_CAPABILITY)
    return result


def prepare_physical_postgres_promotion(
    *,
    config: PhysicalPostgresPromotionCoordinatorConfig,
    prior_activation: VerifiedObjectDeltaRoleMatrixActivation,
    current_witnessed_term: VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    supplied_physical_wal_eligibility: PhysicalWalPromotionAssessment,
    verified_physical_wal_evidence: VerifiedPhysicalWalPromotionEvidence,
    verified_remote_ack: VerifiedPhysicalWalRemoteAckEvidence,
    verified_pre_cas_blob_acceptance: VerifiedPhysicalBlobPreCasAcceptance,
    pre_cas_acceptance_config: PhysicalBlobPreCasAcceptanceConfig,
    now: datetime,
) -> PreparedPhysicalPostgresPromotion:
    """Prepare one promotion only after independently rechecking all proofs.

    Raw receipts are intentionally absent from this signature.  The supplied
    physical-WAL eligibility is checked and independently recomputed from the
    opaque signed WAL/remote-ack inputs against this exact current term.
    """

    return _prepare(
        config=config,
        prior_activation=prior_activation,
        current_witnessed_term=current_witnessed_term,
        supplied_physical_wal_eligibility=supplied_physical_wal_eligibility,
        verified_physical_wal_evidence=verified_physical_wal_evidence,
        verified_remote_ack=verified_remote_ack,
        verified_pre_cas_blob_acceptance=verified_pre_cas_blob_acceptance,
        pre_cas_acceptance_config=pre_cas_acceptance_config,
        now=now,
    )


def _prepared_projection(value: PreparedPhysicalPostgresPromotion) -> tuple[object, ...]:
    return (
        value.schema,
        value.source_site,
        value.target_site,
        value.campaign_id,
        value.release_sha,
        value.stream_generation_id,
        value.baseline_generation_id,
        value.baseline_manifest_sha256,
        value.baseline_wal_lsn,
        value.acknowledged_durable_wal_lsn,
        value.receiver_replay_wal_lsn,
        value.physical_wal_blob_frontier_wal_lsn,
        value.source_writer_epoch,
        value.source_writer_lease_id,
        value.source_witnessed_term_proof_sha256,
        value.candidate_writer_epoch,
        value.candidate_writer_lease_id,
        value.candidate_witness_transition_id,
        value.candidate_witnessed_term_proof_sha256,
        value.destination_age_recipient,
        value.blob_timeline_id,
        value.blob_mapping_receipt_sha256,
        value.blob_mapping_object_key,
        value.blob_mapping_object_version_id,
        value.blob_mapping_ciphertext_sha256,
        value.blob_mapping_eligible_replay_wal_lsn,
        value.blob_route_binding_sha256,
    )


def require_prepared_physical_postgres_promotion(
    value: object,
    *,
    now: datetime,
) -> PreparedPhysicalPostgresPromotion:
    """Recompute and compare a local preparation before any future hand-off."""

    if (
        type(value) is not PreparedPhysicalPostgresPromotion
        or value._capability is not _PREPARED_PROMOTION_CAPABILITY
    ):
        _fail("PREPARED_PROMOTION_UNAUTHORIZED")
    observed_at = _utc(now, reason_code="COORDINATOR_CLOCK_INVALID")
    prepared_at = _utc(value.prepared_at, reason_code="PREPARED_PROMOTION_TIMESTAMP_INVALID")
    if prepared_at > observed_at:
        _fail("PREPARED_PROMOTION_TIMESTAMP_IN_FUTURE")
    fresh = _prepare(
        config=value.coordinator_config,
        prior_activation=value.prior_activation,
        current_witnessed_term=value.current_witnessed_term,
        supplied_physical_wal_eligibility=value.supplied_physical_wal_eligibility,
        verified_physical_wal_evidence=value.verified_physical_wal_evidence,
        verified_remote_ack=value.verified_remote_ack,
        verified_pre_cas_blob_acceptance=value.verified_pre_cas_blob_acceptance,
        pre_cas_acceptance_config=value.pre_cas_acceptance_config,
        now=observed_at,
    )
    if _prepared_projection(value) != _prepared_projection(fresh):
        _fail("PREPARED_PROMOTION_TAMPERED_OR_STALE")
    return value


def _runtime_adapters(value: object) -> PhysicalPostgresPromotionRuntimeAdapters:
    if type(value) is not PhysicalPostgresPromotionRuntimeAdapters:
        _fail("RUNTIME_ADAPTER_SET_INVALID")
    required = (
        ("witness_cas", "consume_promotion_term", "RUNTIME_ADAPTER_WITNESS_CAS_MISSING"),
        (
            "former_writer_fence",
            "fence_former_writer",
            "RUNTIME_ADAPTER_FORMER_WRITER_FENCE_MISSING",
        ),
        (
            "target_recovery",
            "recover_and_promote_target",
            "RUNTIME_ADAPTER_TARGET_RECOVERY_MISSING",
        ),
        ("traffic_fence", "switch_fenced_traffic", "RUNTIME_ADAPTER_TRAFFIC_FENCE_MISSING"),
        (
            "promotion_database_transaction",
            "run_promotion_transaction",
            "RUNTIME_ADAPTER_DATABASE_TRANSACTION_MISSING",
        ),
    )
    reasons = tuple(
        reason
        for field_name, method_name, reason in required
        if not callable(getattr(getattr(value, field_name, None), method_name, None))
    )
    if reasons:
        _fail(",".join(reasons))
    return value


def prepare_physical_postgres_promotion_execution_boundary(
    *,
    prepared_promotion: PreparedPhysicalPostgresPromotion,
    runtime_adapters: PhysicalPostgresPromotionRuntimeAdapters,
    now: datetime,
) -> PreparedPhysicalPostgresPromotionExecutionBoundary:
    """Check that all live responsibilities are explicitly injected.

    This does **not** call any adapter.  It is intentionally an execution
    boundary description, not an execution function.
    """

    prepared = require_prepared_physical_postgres_promotion(
        prepared_promotion,
        now=now,
    )
    adapters = _runtime_adapters(runtime_adapters)
    result = PreparedPhysicalPostgresPromotionExecutionBoundary(
        prepared_promotion=prepared,
        runtime_adapters=adapters,
    )
    object.__setattr__(result, "_capability", _PREPARED_EXECUTION_BOUNDARY_CAPABILITY)
    return result


def require_prepared_physical_postgres_promotion_execution_boundary(
    value: object,
    *,
    now: datetime,
) -> PreparedPhysicalPostgresPromotionExecutionBoundary:
    """Recheck the non-executing boundary and all explicit interfaces."""

    if (
        type(value) is not PreparedPhysicalPostgresPromotionExecutionBoundary
        or value._capability is not _PREPARED_EXECUTION_BOUNDARY_CAPABILITY
    ):
        _fail("PREPARED_EXECUTION_BOUNDARY_UNAUTHORIZED")
    prepared = require_prepared_physical_postgres_promotion(
        value.prepared_promotion,
        now=now,
    )
    adapters = _runtime_adapters(value.runtime_adapters)
    if prepared is not value.prepared_promotion or adapters is not value.runtime_adapters:
        _fail("PREPARED_EXECUTION_BOUNDARY_TAMPERED")
    return value
