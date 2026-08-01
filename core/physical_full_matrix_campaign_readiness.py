"""Fail-closed, local-only readiness aggregation for the physical Full Matrix.

The historical production/staging Full-Matrix runners model a two-server,
logical-sync test workflow.  They are deliberately not inputs to this module.
This module instead accepts already-verified, typed observations for the
``webapp_fi -> private versioned Object Storage -> webapp_ir`` physical data
plane and reports whether every *local evidence slot* is present and bound to
one campaign.

It is intentionally not an execution coordinator.  It never opens a path,
queries PostgreSQL or Witness, contacts Object Storage, invokes a transport,
starts a replay, fences a writer, changes a route, promotes a standby, or runs
the Full Matrix.  Its positive status is named
``all-local-evidence-observed`` rather than ``ready`` and it never grants
external-effect, write, promotion, or execution authority.

Several inputs below are opaque capabilities minted by their owning boundary.
The few observations for which no owning capability exists yet are rechecked
as narrowly typed injected evidence.  That makes the missing runtime adapter
visible as a blocker instead of silently turning a Python data class into an
operational permit.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
from uuid import UUID
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    STREAM_GENERATION_ID_RE,
    canonical_json_bytes,
)
from core.dedicated_host_preflight_aggregate import (
    PREFLIGHT_AGGREGATE_SCHEMA,
    ROLE_ORDER,
    DedicatedHostPreflightAggregateError,
    validate_preflight_aggregate,
)
from core.external_effect_execution_gate import (
    EXTERNAL_EFFECT_EXECUTION_SCOPES,
    RECONCILIATION_DECISION_COMPLETE_NO_RESEND,
    ExternalEffectExecutionAuthorization,
    ExternalEffectExecutionGateError,
    external_effect_execution_authorization_mapping,
    parse_external_effect_execution_authorization,
)
from core.object_delta_role_matrix import (
    OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER,
    OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE,
)
from core.object_delta_role_matrix_rollover import (
    ObjectDeltaRoleMatrixRolloverError,
    VerifiedObjectDeltaRoleMatrixActivation,
    VerifiedObjectDeltaRoleMatrixWitnessedTerm,
    project_active_object_delta_role_matrix_role,
    require_live_object_delta_role_matrix_activation,
    require_live_object_delta_role_matrix_witnessed_term,
)
from core.object_delta_transport_binding import AGE_RECIPIENT_RE
from core.physical_blob_object_storage_uploader import (
    PhysicalBlobObjectStorageUploaderError,
    VerifiedPhysicalBlobObjectStorageBinding,
    require_verified_physical_blob_object_storage_binding,
)
from core.physical_blob_receiver_promotion_evidence import (
    PhysicalBlobReceiverPromotionEvidenceConfig,
    PhysicalBlobReceiverPromotionEvidenceError,
    VerifiedPhysicalBlobReceiverPromotionEvidence,
    require_verified_physical_blob_receiver_promotion_evidence,
)
from core.physical_full_matrix_v2_recovery_evidence import (
    PhysicalFullMatrixV2RecoveryEvidenceError,
    VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    require_verified_physical_full_matrix_v2_recovery_evidence,
)
from core.physical_postgres_recovery_preflight import (
    PHYSICAL_POSTGRES_RECOVERY_PREFLIGHT_SCHEMA,
    PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED,
    PhysicalPostgresRecoveryPreflightResult,
)
from core.physical_wal_object_manifest import (
    PhysicalWalObjectManifestError,
    VerifiedPhysicalWalObjectStorageBundle,
    require_verified_physical_wal_object_storage_bundle,
)
from core.physical_wal_remote_ack import (
    PhysicalWalRemoteAckBinding,
    PhysicalWalRemoteAckError,
    VerifiedPhysicalWalRemoteAckEvidence,
    verify_physical_wal_remote_ack_request,
    require_verified_physical_wal_remote_ack_evidence,
)
from core.physical_wal_remote_ack_receiver_ledger import (
    PhysicalWalRemoteAckReceiverLedgerError,
    PhysicalWalRemoteAckReceiverLedgerResult,
    VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence,
    require_verified_physical_wal_remote_ack_receiver_recovery_evidence,
)
from core.physical_wal_chunked_base_backup_transfer import (
    PhysicalWalChunkedBaseBackupBinding,
)
__all__ = (
    "DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS",
    "LEGACY_FULL_MATRIX_RUNNER_PATHS",
    "LEGACY_FULL_MATRIX_RUNNER_SCHEMAS",
    "PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA",
    "PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED",
    "PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED",
    "PHYSICAL_FULL_MATRIX_DIRECTION_PAIRS",
    "PHYSICAL_FULL_MATRIX_RECOVERY_OBSERVATION_SCHEMA",
    "PHYSICAL_FULL_MATRIX_SOURCE_FENCE_RECOVERY_ROUTE_SCHEMA",
    "PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA",
    "PHYSICAL_FULL_MATRIX_V2_CHUNKED_RECOVERY_EVIDENCE_MISMATCH_REASON",
    "PHYSICAL_FULL_MATRIX_V2_CHUNKED_RECOVERY_EVIDENCE_MISSING_REASON",
    "PHYSICAL_FULL_MATRIX_V2_CHUNKED_RECOVERY_EVIDENCE_SLOT",
    "PHYSICAL_FULL_MATRIX_V2_STRICT_REMOTE_ACK_CHAIN_FENCE_REASON",
    "PHYSICAL_FULL_MATRIX_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_FENCE_REASON",
    "PhysicalFullMatrixCampaignBinding",
    "PhysicalFullMatrixCampaignInputs",
    "PhysicalFullMatrixCampaignReadiness",
    "PhysicalFullMatrixCampaignReadinessConfig",
    "PhysicalFullMatrixCampaignReadinessError",
    "PhysicalFullMatrixDeploymentPreflightPosture",
    "PhysicalFullMatrixExternalEffectReconciliation",
    "PhysicalFullMatrixRecoveryObservation",
    "PhysicalFullMatrixSourceFenceRecoveryRouteObservation",
    "PhysicalFullMatrixStrictRemoteAckWriterResponseObservation",
    "VerifiedPhysicalFullMatrixDeploymentPreflightPosture",
    "VerifiedPhysicalFullMatrixExternalEffectReconciliation",
    "VerifiedPhysicalFullMatrixCampaignReadiness",
    "VerifiedPhysicalFullMatrixRecoveryObservation",
    "VerifiedPhysicalFullMatrixSourceFenceRecoveryRoute",
    "VerifiedPhysicalFullMatrixStrictRemoteAckWriterResponse",
    "assess_physical_full_matrix_campaign_readiness",
    "mint_verified_physical_full_matrix_campaign_readiness",
    "require_verified_physical_full_matrix_deployment_preflight_posture",
    "require_verified_physical_full_matrix_external_effect_reconciliation",
    "require_verified_physical_full_matrix_campaign_readiness",
    "require_verified_physical_full_matrix_recovery_observation",
    "require_verified_physical_full_matrix_source_fence_recovery_route",
    "require_verified_physical_full_matrix_strict_remote_ack_writer_response",
    "verify_physical_full_matrix_deployment_preflight_posture",
    "verify_physical_full_matrix_external_effect_reconciliation",
    "verify_physical_full_matrix_recovery_observation",
    "verify_physical_full_matrix_source_fence_recovery_route",
    "verify_physical_full_matrix_strict_remote_ack_writer_response",
)


PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA = (
    "gold-trade-physical-full-matrix-campaign-readiness-v1"
)
PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_DEFAULT_ENABLED = False
PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED = "blocked"
PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED = (
    "all-local-evidence-observed"
)

PHYSICAL_FULL_MATRIX_RECOVERY_OBSERVATION_SCHEMA = (
    "gold-trade-physical-full-matrix-recovery-observation-v1"
)

PHYSICAL_FULL_MATRIX_SOURCE_FENCE_RECOVERY_ROUTE_SCHEMA = (
    "gold-trade-physical-full-matrix-source-fence-recovery-route-v1"
)
PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA = (
    "gold-trade-physical-full-matrix-strict-remote-ack-writer-response-v1"
)
# ``VerifiedPhysicalWalObjectStorageBundle`` carries exactly one
# ``baseline.base_backup_object`` and therefore describes the retired V1
# single-object bootstrap shape.  No V2 receiver/admission contract exists in
# this readiness boundary yet.  Keep the fence explicit rather than allowing
# a future otherwise-valid V1 bundle to become a positive Full-Matrix result.
PHYSICAL_FULL_MATRIX_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_FENCE_REASON = (
    "v1-single-object-base-backup-activation-fenced"
)

# This is a diagnostic V2 recovery/coverage observation, not a V2 remote-ack
# protocol.  Keep the names public and stable so operators can distinguish a
# missing V2 recovery substrate from the separately missing V2 ACK chain.
PHYSICAL_FULL_MATRIX_V2_CHUNKED_RECOVERY_EVIDENCE_SLOT = (
    "v2-chunked-recovery-evidence"
)
PHYSICAL_FULL_MATRIX_V2_CHUNKED_RECOVERY_EVIDENCE_MISSING_REASON = (
    "missing-v2-chunked-recovery-evidence"
)
PHYSICAL_FULL_MATRIX_V2_CHUNKED_RECOVERY_EVIDENCE_MISMATCH_REASON = (
    "v2-chunked-recovery-evidence-mismatch"
)
# The V2 bridge deliberately has no source request, receiver receipt, durable
# ledger, or writer-response coupling.  Keep that implementation gap as an
# explicit fence rather than allowing the observed recovery slot to imply an
# acknowledgement protocol exists.
PHYSICAL_FULL_MATRIX_V2_STRICT_REMOTE_ACK_CHAIN_FENCE_REASON = (
    "v2-strict-remote-ack-chain-not-integrated"
)

# A Full Matrix has two distinct physical directions.  The first starts with
# FI as writer and IR as standby; after a witnessed promotion the failback
# direction is IR as writer and FI as standby.  Keep the legacy named
# constants for callers that construct the normal-direction readiness input,
# but never use them to silently coerce a reverse binding back to FI -> IR.
PHYSICAL_FULL_MATRIX_SOURCE_SITE = "webapp_fi"
PHYSICAL_FULL_MATRIX_DESTINATION_SITE = "webapp_ir"
PHYSICAL_FULL_MATRIX_DIRECTION_PAIRS = frozenset(
    {
        ("webapp_fi", "webapp_ir"),
        ("webapp_ir", "webapp_fi"),
    }
)
PHYSICAL_FULL_MATRIX_SOURCE_WRITE_FENCE_MODE = "term-fenced-before-commit-v1"
PHYSICAL_FULL_MATRIX_RECOVERY_ROUTE = (
    "private-versioned-object-storage-pull-only-v1"
)
PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_WRITER_RESPONSE_MODE = (
    "strict-remote-durable-replay-before-local-ack-v1"
)
PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_RECEIVER_RESPONSE_SOURCE = (
    "durable-ledger-receipt-v1"
)

DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS = 90
MAX_PHYSICAL_FULL_MATRIX_EVIDENCE_AGE_SECONDS = 300
MAX_PHYSICAL_FULL_MATRIX_FUTURE_SKEW_SECONDS = 5

# The new driver rejects any nonempty historical artifact collection before
# looking at it.  The identifiers are retained only for human diagnostics and
# documentation; parsing an old plan would create an accidental compatibility
# path.
LEGACY_FULL_MATRIX_RUNNER_SCHEMAS = frozenset(
    {
        "production_full_matrix_runner_plan_v1",
        "production_full_matrix_manifest_v1",
        "production_full_matrix_plan_v1",
        "staging_two_server_full_matrix_runner_v1",
        "staging_two_server_full_matrix_manifest_v1",
        "bot_webapp_candidate_full_matrix_v1",
    }
)
LEGACY_FULL_MATRIX_RUNNER_PATHS = frozenset(
    {
        "scripts/run_production_full_matrix.py",
        "scripts/build_production_full_matrix_manifest.py",
        "scripts/plan_production_full_matrix.py",
        "scripts/run_staging_two_server_full_matrix.py",
        "scripts/build_staging_two_server_full_matrix_manifest.py",
        "scripts/run_bot_webapp_candidate_full_matrix.py",
    }
)

_LSN_RE = re.compile(
    r"^(?:0|[1-9A-F][0-9A-F]{0,7})/(?:0|[1-9A-F][0-9A-F]{0,7})$",
    re.ASCII,
)
_SCHEMA_REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)
_OBSERVATION_STATUS = "observed"
_VERIFIED_DEPLOYMENT_CAPABILITY = object()
_VERIFIED_EXTERNAL_EFFECT_CAPABILITY = object()
_VERIFIED_FENCE_CAPABILITY = object()
_VERIFIED_RECOVERY_CAPABILITY = object()
_VERIFIED_STRICT_ACK_WRITER_RESPONSE_CAPABILITY = object()
_VERIFIED_READINESS_CAPABILITY = object()


class PhysicalFullMatrixCampaignReadinessError(ValueError):
    """Raised only by narrow verifier helpers, never by the aggregator."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalFullMatrixCampaignBinding:
    """One exact normal-direction FI-to-IR physical campaign identity.

    The binding intentionally names a schema revision even though the physical
    WAL stream carries database bytes: a reviewed campaign must not silently
    run a release/schema combination different from the one that produced its
    baseline.  ``p0_operation_id`` is the UUID produced by the selected
    auth/upload transaction participants, not an execution token.
    """

    campaign_id: str
    release_sha: str
    schema_revision: str
    source_site: str
    destination_site: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    timeline_id: int
    stream_generation_id: str
    destination_age_recipient: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    target_acknowledged_wal_lsn: str
    blob_object_frontier_wal_lsn: str
    recovery_stage_bundle_id: str
    recovery_stage_receipt_sha256: str
    deployment_operation_id: str
    deployment_manifest_sha256: str
    p0_operation_id: UUID


@dataclass(frozen=True)
class PhysicalFullMatrixCampaignReadinessConfig:
    """Default-off local policy; it does not contain secrets or endpoints."""

    binding: PhysicalFullMatrixCampaignBinding | None = None
    enabled: bool = PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_DEFAULT_ENABLED
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS


@dataclass(frozen=True)
class PhysicalFullMatrixRecoveryObservation:
    """Freshness envelope for an injected physical PostgreSQL recovery result.

    ``PhysicalPostgresRecoveryPreflightResult`` intentionally has no
    observation timestamp because it is a compact result type. This envelope
    supplies a separate typed, freshness-bound observation; it does not run a
    PostgreSQL readback or replay.
    """

    schema: str
    status: str
    recovery_result: object
    recovery_evidence_sha256: str
    observed_at: datetime


@dataclass(frozen=True)
class VerifiedPhysicalFullMatrixRecoveryObservation:
    """Opaque fresh recovery observation, not a replay or promotion permit."""

    observation: PhysicalFullMatrixRecoveryObservation
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalFullMatrixDeploymentPreflightPosture:
    """Typed input for four already-collected read-only host receipts.

    This class carries only safe receipt mappings.  The verifier validates the
    existing aggregate contract and never opens a manifest path or runs a host
    probe.
    """

    validated_manifest: object
    receipts: object


@dataclass(frozen=True)
class VerifiedPhysicalFullMatrixDeploymentPreflightPosture:
    """Opaque normalized deployment posture, still observation-only."""

    canonical_validated_manifest: bytes
    canonical_receipts: tuple[bytes, ...]
    aggregate_sha256: str
    newest_observed_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalFullMatrixSourceFenceRecoveryRouteObservation:
    """Injected proof-shaped observation of the source fence/recovery route.

    An owning runtime adapter must establish these facts.  This module only
    binds and freshness-checks its supplied non-secret projection.
    """

    schema: str
    status: str
    campaign_id: str
    release_sha: str
    schema_revision: str
    source_site: str
    destination_site: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    timeline_id: int
    stream_generation_id: str
    destination_age_recipient: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    source_write_fence_mode: str
    recovery_route: str
    direct_fi_to_ir_control: str
    legacy_runner_compatibility: str
    observed_at: datetime
    evidence_sha256: str


@dataclass(frozen=True)
class VerifiedPhysicalFullMatrixSourceFenceRecoveryRoute:
    """Opaque route/fence observation, never a fence or route capability."""

    observation: PhysicalFullMatrixSourceFenceRecoveryRouteObservation
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalFullMatrixStrictRemoteAckWriterResponseObservation:
    """Injected evidence that strict remote-ack is in the writer response path.

    The signed request/receipt and IR durable ledger prove a narrow remote
    continuity point.  They do not prove that FI withheld an application
    acknowledgement.  This explicit slot keeps that missing implementation
    boundary a hard blocker until a reviewed adapter provides an exact
    observation.
    """

    schema: str
    status: str
    campaign_id: str
    release_sha: str
    schema_revision: str
    source_site: str
    destination_site: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    timeline_id: int
    stream_generation_id: str
    destination_age_recipient: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    target_acknowledged_wal_lsn: str
    blob_object_frontier_wal_lsn: str
    writer_response_mode: str
    receiver_response_source: str
    durable_commit_coupled: bool
    fences_writes_when_ack_unavailable: bool
    observed_at: datetime
    evidence_sha256: str


@dataclass(frozen=True)
class VerifiedPhysicalFullMatrixStrictRemoteAckWriterResponse:
    """Opaque strict-writer-response observation, not a write permit."""

    observation: PhysicalFullMatrixStrictRemoteAckWriterResponseObservation
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class VerifiedPhysicalFullMatrixExternalEffectReconciliation:
    """Opaque term-bound reconciliation projection, never worker authority."""

    authorization: ExternalEffectExecutionAuthorization
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(frozen=True)
class PhysicalFullMatrixCampaignInputs:
    """All evidence is injected; absence is deliberately distinguishable.

    No field may be substituted with an old runner plan or a raw filesystem
    path.  The Blob config is intentionally required separately so receiver
    evidence is rechecked against current signer pins every time.
    """

    recovery_observation: object | None = None
    # The opaque V2 bridge joins chunked base-backup, exact WAL/Blob coverage,
    # and target recovery readback.  It deliberately cannot fill any legacy
    # V1 remote-ack/ledger/writer-response slot; a separate V2 ACK protocol
    # must replace those slots atomically in a later change.
    v2_recovery_evidence: object | None = None
    physical_wal_bundle: object | None = None
    remote_ack_evidence: object | None = None
    remote_ack_receiver_recovery: object | None = None
    remote_ack_durable_ledger: object | None = None
    strict_remote_ack_writer_response: object | None = None
    arvan_immutability_preflight: object | None = None
    arvan_immutability_preflight_binding: object | None = None
    # The two-role normal-direction immutability contract remains readable
    # only as historical evidence.  It is deliberately *not* a Full-Matrix
    # readiness input: a reversible campaign must prove both directional
    # publisher/receiver pairs under four independent identities.
    arvan_four_role_immutability_preflight: object | None = None
    arvan_four_role_immutability_preflight_config: object | None = None
    # The owning four-role immutability boundary deliberately receives these
    # separately.  A raw IAM gate, unbound live-IAM record, or a failback
    # binding from another route must not become readiness evidence merely by
    # being nested inside a familiar preflight object.
    arvan_four_role_immutability_live_iam_durable_admission: object | None = None
    arvan_four_role_immutability_live_iam_binding: object | None = None
    arvan_four_role_immutability_failback_binding: object | None = None
    arvan_failback_preflight: object | None = None
    arvan_failback_preflight_config: object | None = None
    blob_promotion_evidence: object | None = None
    blob_storage_binding: object | None = None
    blob_promotion_config: object | None = None
    witnessed_term: object | None = None
    role_activation: object | None = None
    deployment_preflight_posture: object | None = None
    p0_auth_upload_result: object | None = None
    external_effect_reconciliation: object | None = None
    source_fence_recovery_route: object | None = None
    legacy_runner_artifacts: object = ()


@dataclass(frozen=True)
class PhysicalFullMatrixCampaignReadiness:
    """Deterministic non-authorizing report from one local assessment.

    This remains a display/reporting value.  It is intentionally not an input
    to the execution driver: ordinary Python callers can construct data
    classes, so a driver must require the separate process-local verified
    capability below before it may consider the report.
    """

    schema: str
    status: str
    reason_codes: tuple[str, ...]
    campaign_id: str | None
    release_sha: str | None
    binding_sha256: str | None
    observed_slots: tuple[str, ...]
    external_execution_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalFullMatrixCampaignReadiness:
    """Opaque process-local provenance for one positive readiness report.

    The associated configuration and injected evidence stay in a private weak
    state table rather than the public report.  A future execution boundary
    therefore has to re-run the no-I/O readiness assessment at its own clock
    before it can use this object.  This is neither execution nor promotion
    authority and is deliberately not serializable across processes.
    """

    report: PhysicalFullMatrixCampaignReadiness
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SERIALIZATION_FORBIDDEN")


@dataclass(frozen=True)
class _VerifiedPhysicalFullMatrixCampaignReadinessState:
    config: object
    inputs: object
    report: PhysicalFullMatrixCampaignReadiness


_VERIFIED_READINESS_STATES: WeakKeyDictionary[
    VerifiedPhysicalFullMatrixCampaignReadiness,
    _VerifiedPhysicalFullMatrixCampaignReadinessState,
] = WeakKeyDictionary()


@dataclass(frozen=True)
class _BindingFacts:
    campaign_id: str
    release_sha: str
    schema_revision: str
    source_site: str
    destination_site: str
    baseline_generation_id: str
    baseline_manifest_sha256: str
    baseline_wal_lsn: str
    baseline_wal_lsn_value: int
    timeline_id: int
    stream_generation_id: str
    destination_age_recipient: str
    route_binding_sha256: str
    writer_epoch: int
    writer_lease_id: str
    witness_transition_id: str
    witnessed_term_proof_sha256: str
    target_acknowledged_wal_lsn: str
    target_acknowledged_wal_lsn_value: int
    blob_object_frontier_wal_lsn: str
    blob_object_frontier_wal_lsn_value: int
    recovery_stage_bundle_id: str
    recovery_stage_receipt_sha256: str
    deployment_operation_id: str
    deployment_manifest_sha256: str
    p0_operation_id: UUID
    binding_sha256: str


def _fail(code: str) -> None:
    raise PhysicalFullMatrixCampaignReadinessError(code)


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _fresh(value: object, *, now: datetime, maximum_age_seconds: int, code: str) -> datetime:
    observed = _utc(value, code=code)
    if observed > now + timedelta(seconds=MAX_PHYSICAL_FULL_MATRIX_FUTURE_SKEW_SECONDS):
        _fail(code)
    if observed < now - timedelta(seconds=maximum_age_seconds):
        _fail(code)
    return observed


def _text(value: object, *, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    return value


def _sha256(value: object, *, code: str) -> str:
    result = _text(value, pattern=SHA256_RE, code=code)
    if result == "0" * 64:
        _fail(code)
    return result


def _identifier(value: object, *, code: str) -> str:
    return _text(value, pattern=_SCHEMA_REVISION_RE, code=code)


def _lsn(value: object, *, code: str) -> tuple[str, int]:
    text = _text(value, pattern=_LSN_RE, code=code)
    high, low = text.split("/", 1)
    return text, (int(high, 16) << 32) | int(low, 16)


def _uuid(value: object, *, code: str) -> UUID:
    if type(value) is not UUID or value.int == 0:
        _fail(code)
    return value


def _maximum_evidence_age(value: object, *, code: str) -> int:
    if (
        type(value) is not int
        or not 1 <= value <= MAX_PHYSICAL_FULL_MATRIX_EVIDENCE_AGE_SECONDS
    ):
        _fail(code)
    return value


def _normalise_binding(value: object) -> _BindingFacts:
    if type(value) is not PhysicalFullMatrixCampaignBinding:
        _fail("INVALID_CAMPAIGN_BINDING")
    campaign_id = _text(value.campaign_id, pattern=CAMPAIGN_ID_RE, code="INVALID_CAMPAIGN_BINDING")
    release_sha = _text(value.release_sha, pattern=RELEASE_SHA_RE, code="INVALID_CAMPAIGN_BINDING")
    schema_revision = _identifier(value.schema_revision, code="INVALID_CAMPAIGN_BINDING")
    if (value.source_site, value.destination_site) not in PHYSICAL_FULL_MATRIX_DIRECTION_PAIRS:
        _fail("INVALID_CAMPAIGN_BINDING")
    source_site = value.source_site
    destination_site = value.destination_site
    baseline_generation_id = _text(
        value.baseline_generation_id,
        pattern=STREAM_GENERATION_ID_RE,
        code="INVALID_CAMPAIGN_BINDING",
    )
    baseline_manifest_sha256 = _sha256(value.baseline_manifest_sha256, code="INVALID_CAMPAIGN_BINDING")
    baseline_wal_lsn, baseline_wal_lsn_value = _lsn(value.baseline_wal_lsn, code="INVALID_CAMPAIGN_BINDING")
    if type(value.timeline_id) is not int or not 1 <= value.timeline_id <= 0xFFFFFFFF:
        _fail("INVALID_CAMPAIGN_BINDING")
    stream_generation_id = _text(
        value.stream_generation_id,
        pattern=STREAM_GENERATION_ID_RE,
        code="INVALID_CAMPAIGN_BINDING",
    )
    destination_age_recipient = _text(
        value.destination_age_recipient,
        pattern=AGE_RECIPIENT_RE,
        code="INVALID_CAMPAIGN_BINDING",
    )
    route_binding_sha256 = _sha256(value.route_binding_sha256, code="INVALID_CAMPAIGN_BINDING")
    if type(value.writer_epoch) is not int or value.writer_epoch < 1:
        _fail("INVALID_CAMPAIGN_BINDING")
    writer_lease_id = _text(value.writer_lease_id, pattern=LEASE_ID_RE, code="INVALID_CAMPAIGN_BINDING")
    witness_transition_id = _identifier(value.witness_transition_id, code="INVALID_CAMPAIGN_BINDING")
    witnessed_term_proof_sha256 = _sha256(
        value.witnessed_term_proof_sha256,
        code="INVALID_CAMPAIGN_BINDING",
    )
    target_acknowledged_wal_lsn, target_acknowledged_wal_lsn_value = _lsn(
        value.target_acknowledged_wal_lsn,
        code="INVALID_CAMPAIGN_BINDING",
    )
    blob_object_frontier_wal_lsn, blob_object_frontier_wal_lsn_value = _lsn(
        value.blob_object_frontier_wal_lsn,
        code="INVALID_CAMPAIGN_BINDING",
    )
    if (
        target_acknowledged_wal_lsn_value < baseline_wal_lsn_value
        or blob_object_frontier_wal_lsn_value < target_acknowledged_wal_lsn_value
    ):
        _fail("INVALID_CAMPAIGN_BINDING")
    recovery_stage_bundle_id = _sha256(value.recovery_stage_bundle_id, code="INVALID_CAMPAIGN_BINDING")
    recovery_stage_receipt_sha256 = _sha256(
        value.recovery_stage_receipt_sha256,
        code="INVALID_CAMPAIGN_BINDING",
    )
    deployment_operation_id = _identifier(value.deployment_operation_id, code="INVALID_CAMPAIGN_BINDING")
    deployment_manifest_sha256 = _sha256(
        value.deployment_manifest_sha256,
        code="INVALID_CAMPAIGN_BINDING",
    )
    p0_operation_id = _uuid(value.p0_operation_id, code="INVALID_CAMPAIGN_BINDING")
    payload = {
        "campaign_id": campaign_id,
        "release_sha": release_sha,
        "schema_revision": schema_revision,
        "source_site": source_site,
        "destination_site": destination_site,
        "baseline_generation_id": baseline_generation_id,
        "baseline_manifest_sha256": baseline_manifest_sha256,
        "baseline_wal_lsn": baseline_wal_lsn,
        "timeline_id": value.timeline_id,
        "stream_generation_id": stream_generation_id,
        "destination_age_recipient": destination_age_recipient,
        "route_binding_sha256": route_binding_sha256,
        "writer_epoch": value.writer_epoch,
        "writer_lease_id": writer_lease_id,
        "witness_transition_id": witness_transition_id,
        "witnessed_term_proof_sha256": witnessed_term_proof_sha256,
        "target_acknowledged_wal_lsn": target_acknowledged_wal_lsn,
        "blob_object_frontier_wal_lsn": blob_object_frontier_wal_lsn,
        "recovery_stage_bundle_id": recovery_stage_bundle_id,
        "recovery_stage_receipt_sha256": recovery_stage_receipt_sha256,
        "deployment_operation_id": deployment_operation_id,
        "deployment_manifest_sha256": deployment_manifest_sha256,
        "p0_operation_id": str(p0_operation_id),
    }
    return _BindingFacts(
        campaign_id=campaign_id,
        release_sha=release_sha,
        schema_revision=schema_revision,
        source_site=source_site,
        destination_site=destination_site,
        baseline_generation_id=baseline_generation_id,
        baseline_manifest_sha256=baseline_manifest_sha256,
        baseline_wal_lsn=baseline_wal_lsn,
        baseline_wal_lsn_value=baseline_wal_lsn_value,
        timeline_id=value.timeline_id,
        stream_generation_id=stream_generation_id,
        destination_age_recipient=destination_age_recipient,
        route_binding_sha256=route_binding_sha256,
        writer_epoch=value.writer_epoch,
        writer_lease_id=writer_lease_id,
        witness_transition_id=witness_transition_id,
        witnessed_term_proof_sha256=witnessed_term_proof_sha256,
        target_acknowledged_wal_lsn=target_acknowledged_wal_lsn,
        target_acknowledged_wal_lsn_value=target_acknowledged_wal_lsn_value,
        blob_object_frontier_wal_lsn=blob_object_frontier_wal_lsn,
        blob_object_frontier_wal_lsn_value=blob_object_frontier_wal_lsn_value,
        recovery_stage_bundle_id=recovery_stage_bundle_id,
        recovery_stage_receipt_sha256=recovery_stage_receipt_sha256,
        deployment_operation_id=deployment_operation_id,
        deployment_manifest_sha256=deployment_manifest_sha256,
        p0_operation_id=p0_operation_id,
        binding_sha256=hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    )


def _normalise_config(value: object) -> tuple[_BindingFacts, int]:
    if type(value) is not PhysicalFullMatrixCampaignReadinessConfig:
        _fail("INVALID_CAMPAIGN_BINDING")
    if value.enabled is not True:
        _fail("DRIVER_DISABLED")
    return _normalise_binding(value.binding), _maximum_evidence_age(
        value.maximum_evidence_age_seconds,
        code="INVALID_CAMPAIGN_BINDING",
    )


def _normalise_now(value: object) -> datetime:
    return _utc(value, code="INVALID_ASSESSMENT_CLOCK")


def _canonical_mapping(value: object, *, code: str) -> bytes:
    if not isinstance(value, Mapping):
        _fail(code)
    try:
        return canonical_json_bytes(dict(value))
    except (TypeError, ValueError):
        _fail(code)


def _decode_canonical_mapping(value: object, *, code: str) -> dict[str, Any]:
    if not isinstance(value, bytes) or not value:
        _fail(code)
    try:
        decoded = json.loads(value.decode("ascii", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(decoded, dict):
        _fail(code)
    try:
        if canonical_json_bytes(decoded) != value:
            _fail(code)
    except (TypeError, ValueError):
        _fail(code)
    return decoded


def _parse_preflight_observed_at(value: object, *, code: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail(code)
    return _utc(parsed, code=code)


def _deployment_posture_facts(
    *,
    validated_manifest: object,
    receipts: object,
    binding: _BindingFacts,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> tuple[bytes, tuple[bytes, ...], str, datetime]:
    try:
        aggregate = validate_preflight_aggregate(validated_manifest, receipts)
    except DedicatedHostPreflightAggregateError as exc:
        raise PhysicalFullMatrixCampaignReadinessError("DEPLOYMENT_PREFLIGHT_INVALID") from exc
    if (
        aggregate.get("schema") != PREFLIGHT_AGGREGATE_SCHEMA
        or aggregate.get("status") != "observations-aggregated"
        or aggregate.get("decision") != "not-evaluated"
        or aggregate.get("campaign_id") != binding.campaign_id
        or aggregate.get("operation_id") != binding.deployment_operation_id
        or aggregate.get("release_sha") != binding.release_sha
        or aggregate.get("manifest_sha256") != binding.deployment_manifest_sha256
    ):
        _fail("DEPLOYMENT_PREFLIGHT_BINDING_MISMATCH")
    raw_receipts = aggregate.get("receipts")
    if not isinstance(raw_receipts, list) or len(raw_receipts) != len(ROLE_ORDER):
        _fail("DEPLOYMENT_PREFLIGHT_INVALID")
    observed_times: list[datetime] = []
    for expected_role, receipt in zip(ROLE_ORDER, raw_receipts, strict=True):
        if not isinstance(receipt, Mapping) or receipt.get("role") != expected_role:
            _fail("DEPLOYMENT_PREFLIGHT_INVALID")
        observed_times.append(
            _fresh(
                _parse_preflight_observed_at(receipt.get("observed_at"), code="DEPLOYMENT_PREFLIGHT_INVALID"),
                now=now,
                maximum_age_seconds=maximum_evidence_age_seconds,
                code="DEPLOYMENT_PREFLIGHT_INVALID",
            )
        )
        observation = receipt.get("observation")
        if not isinstance(observation, Mapping):
            _fail("DEPLOYMENT_PREFLIGHT_INVALID")
        release = observation.get("release")
        runtime = observation.get("runtime")
        staging_mount = observation.get("staging_mount")
        if not isinstance(release, Mapping) or not isinstance(runtime, Mapping) or not isinstance(staging_mount, Mapping):
            _fail("DEPLOYMENT_PREFLIGHT_INVALID")
        if (
            release.get("state") != "present"
            or release.get("release_sha") != binding.release_sha
            or release.get("clean") is not True
            or runtime.get("current_link_present") is not True
            or type(runtime.get("matrix_process_count")) is not int
            or runtime.get("matrix_process_count") != 0
        ):
            _fail("DEPLOYMENT_PREFLIGHT_POSTURE_MISMATCH")
        if expected_role in {PHYSICAL_FULL_MATRIX_SOURCE_SITE, PHYSICAL_FULL_MATRIX_DESTINATION_SITE}:
            if staging_mount.get("present") is not True or "rw" not in staging_mount.get("options", []):
                _fail("DEPLOYMENT_PREFLIGHT_POSTURE_MISMATCH")
    if not isinstance(validated_manifest, Mapping) or not isinstance(receipts, Sequence) or isinstance(receipts, (str, bytes)):
        _fail("DEPLOYMENT_PREFLIGHT_INVALID")
    canonical_manifest = _canonical_mapping(validated_manifest, code="DEPLOYMENT_PREFLIGHT_INVALID")
    canonical_receipts = tuple(
        _canonical_mapping(item, code="DEPLOYMENT_PREFLIGHT_INVALID") for item in receipts
    )
    if len(canonical_receipts) != len(ROLE_ORDER):
        _fail("DEPLOYMENT_PREFLIGHT_INVALID")
    aggregate_sha256 = hashlib.sha256(canonical_json_bytes(aggregate)).hexdigest()
    return canonical_manifest, canonical_receipts, aggregate_sha256, max(observed_times)


def verify_physical_full_matrix_deployment_preflight_posture(
    value: object,
    *,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS,
) -> VerifiedPhysicalFullMatrixDeploymentPreflightPosture:
    """Verify injected four-host read-only posture without host or file I/O."""

    if type(value) is not PhysicalFullMatrixDeploymentPreflightPosture:
        _fail("DEPLOYMENT_PREFLIGHT_INVALID")
    facts = _normalise_binding(binding)
    observed_now = _utc(now, code="DEPLOYMENT_PREFLIGHT_INVALID")
    maximum = _maximum_evidence_age(maximum_evidence_age_seconds, code="DEPLOYMENT_PREFLIGHT_INVALID")
    canonical_manifest, canonical_receipts, aggregate_sha256, newest_observed_at = _deployment_posture_facts(
        validated_manifest=value.validated_manifest,
        receipts=value.receipts,
        binding=facts,
        now=observed_now,
        maximum_evidence_age_seconds=maximum,
    )
    result = VerifiedPhysicalFullMatrixDeploymentPreflightPosture(
        canonical_validated_manifest=canonical_manifest,
        canonical_receipts=canonical_receipts,
        aggregate_sha256=aggregate_sha256,
        newest_observed_at=newest_observed_at,
    )
    object.__setattr__(result, "_capability", _VERIFIED_DEPLOYMENT_CAPABILITY)
    return result


def require_verified_physical_full_matrix_deployment_preflight_posture(
    value: object,
    *,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS,
) -> VerifiedPhysicalFullMatrixDeploymentPreflightPosture:
    """Revalidate an opaque posture from its canonical injected receipts."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixDeploymentPreflightPosture
        or value._capability is not _VERIFIED_DEPLOYMENT_CAPABILITY
    ):
        _fail("VERIFIED_DEPLOYMENT_PREFLIGHT_REQUIRED")
    raw_manifest = _decode_canonical_mapping(value.canonical_validated_manifest, code="VERIFIED_DEPLOYMENT_PREFLIGHT_TAMPERED")
    raw_receipts = tuple(
        _decode_canonical_mapping(item, code="VERIFIED_DEPLOYMENT_PREFLIGHT_TAMPERED")
        for item in value.canonical_receipts
    )
    normalized = verify_physical_full_matrix_deployment_preflight_posture(
        PhysicalFullMatrixDeploymentPreflightPosture(
            validated_manifest=raw_manifest,
            receipts=raw_receipts,
        ),
        binding=binding,
        now=now,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
    )
    if (
        normalized.canonical_validated_manifest != value.canonical_validated_manifest
        or normalized.canonical_receipts != value.canonical_receipts
        or normalized.aggregate_sha256 != value.aggregate_sha256
        or normalized.newest_observed_at != value.newest_observed_at
    ):
        _fail("VERIFIED_DEPLOYMENT_PREFLIGHT_TAMPERED")
    return value


def _fence_observation_facts(
    value: object,
    *,
    binding: _BindingFacts,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> PhysicalFullMatrixSourceFenceRecoveryRouteObservation:
    if type(value) is not PhysicalFullMatrixSourceFenceRecoveryRouteObservation:
        _fail("SOURCE_FENCE_ROUTE_INVALID")
    if value.schema != PHYSICAL_FULL_MATRIX_SOURCE_FENCE_RECOVERY_ROUTE_SCHEMA or value.status != _OBSERVATION_STATUS:
        _fail("SOURCE_FENCE_ROUTE_INVALID")
    _fresh(value.observed_at, now=now, maximum_age_seconds=maximum_evidence_age_seconds, code="SOURCE_FENCE_ROUTE_INVALID")
    _sha256(value.evidence_sha256, code="SOURCE_FENCE_ROUTE_INVALID")
    if not _source_common_binding_matches(value, binding):
        _fail("SOURCE_FENCE_ROUTE_BINDING_MISMATCH")
    if (
        value.source_write_fence_mode != PHYSICAL_FULL_MATRIX_SOURCE_WRITE_FENCE_MODE
        or value.recovery_route != PHYSICAL_FULL_MATRIX_RECOVERY_ROUTE
        or value.direct_fi_to_ir_control != "forbidden"
        or value.legacy_runner_compatibility != "forbidden"
    ):
        _fail("SOURCE_FENCE_ROUTE_POLICY_MISMATCH")
    return value


def _source_common_binding_matches(value: object, binding: _BindingFacts) -> bool:
    """Compare a projection only after scalar type validation.

    The helper is shared by injected source/fence and strict-writer-response
    observations.  It intentionally permits no alternate route direction.
    """

    try:
        return (
            _text(getattr(value, "campaign_id"), pattern=CAMPAIGN_ID_RE, code="COMMON_BINDING_INVALID") == binding.campaign_id
            and _text(getattr(value, "release_sha"), pattern=RELEASE_SHA_RE, code="COMMON_BINDING_INVALID") == binding.release_sha
            and _identifier(getattr(value, "schema_revision"), code="COMMON_BINDING_INVALID") == binding.schema_revision
            and getattr(value, "source_site") == binding.source_site
            and getattr(value, "destination_site") == binding.destination_site
            and _text(getattr(value, "baseline_generation_id"), pattern=STREAM_GENERATION_ID_RE, code="COMMON_BINDING_INVALID") == binding.baseline_generation_id
            and _sha256(getattr(value, "baseline_manifest_sha256"), code="COMMON_BINDING_INVALID") == binding.baseline_manifest_sha256
            and _lsn(getattr(value, "baseline_wal_lsn"), code="COMMON_BINDING_INVALID")[0] == binding.baseline_wal_lsn
            and type(getattr(value, "timeline_id")) is int
            and getattr(value, "timeline_id") == binding.timeline_id
            and _text(getattr(value, "stream_generation_id"), pattern=STREAM_GENERATION_ID_RE, code="COMMON_BINDING_INVALID") == binding.stream_generation_id
            and _text(getattr(value, "destination_age_recipient"), pattern=AGE_RECIPIENT_RE, code="COMMON_BINDING_INVALID") == binding.destination_age_recipient
            and _sha256(getattr(value, "route_binding_sha256"), code="COMMON_BINDING_INVALID") == binding.route_binding_sha256
            and type(getattr(value, "writer_epoch")) is int
            and getattr(value, "writer_epoch") == binding.writer_epoch
            and _text(getattr(value, "writer_lease_id"), pattern=LEASE_ID_RE, code="COMMON_BINDING_INVALID") == binding.writer_lease_id
            and _identifier(getattr(value, "witness_transition_id"), code="COMMON_BINDING_INVALID") == binding.witness_transition_id
            and _sha256(getattr(value, "witnessed_term_proof_sha256"), code="COMMON_BINDING_INVALID") == binding.witnessed_term_proof_sha256
        )
    except (AttributeError, PhysicalFullMatrixCampaignReadinessError):
        return False


def verify_physical_full_matrix_source_fence_recovery_route(
    value: object,
    *,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS,
) -> VerifiedPhysicalFullMatrixSourceFenceRecoveryRoute:
    """Bind a narrow injected source-fence/recovery-route observation."""

    facts = _normalise_binding(binding)
    observed_now = _utc(now, code="SOURCE_FENCE_ROUTE_INVALID")
    maximum = _maximum_evidence_age(maximum_evidence_age_seconds, code="SOURCE_FENCE_ROUTE_INVALID")
    observation = _fence_observation_facts(
        value,
        binding=facts,
        now=observed_now,
        maximum_evidence_age_seconds=maximum,
    )
    result = VerifiedPhysicalFullMatrixSourceFenceRecoveryRoute(observation=observation)
    object.__setattr__(result, "_capability", _VERIFIED_FENCE_CAPABILITY)
    return result


def require_verified_physical_full_matrix_source_fence_recovery_route(
    value: object,
    *,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS,
) -> VerifiedPhysicalFullMatrixSourceFenceRecoveryRoute:
    """Recheck a fence/route evidence capability; it still performs no fence."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixSourceFenceRecoveryRoute
        or value._capability is not _VERIFIED_FENCE_CAPABILITY
    ):
        _fail("VERIFIED_SOURCE_FENCE_ROUTE_REQUIRED")
    normalized = verify_physical_full_matrix_source_fence_recovery_route(
        value.observation,
        binding=binding,
        now=now,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
    )
    if normalized.observation != value.observation:
        _fail("VERIFIED_SOURCE_FENCE_ROUTE_TAMPERED")
    return value


def _strict_writer_response_facts(
    value: object,
    *,
    binding: _BindingFacts,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> PhysicalFullMatrixStrictRemoteAckWriterResponseObservation:
    if type(value) is not PhysicalFullMatrixStrictRemoteAckWriterResponseObservation:
        _fail("STRICT_REMOTE_ACK_WRITER_RESPONSE_INVALID")
    if value.schema != PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_WRITER_RESPONSE_SCHEMA or value.status != _OBSERVATION_STATUS:
        _fail("STRICT_REMOTE_ACK_WRITER_RESPONSE_INVALID")
    _fresh(value.observed_at, now=now, maximum_age_seconds=maximum_evidence_age_seconds, code="STRICT_REMOTE_ACK_WRITER_RESPONSE_INVALID")
    _sha256(value.evidence_sha256, code="STRICT_REMOTE_ACK_WRITER_RESPONSE_INVALID")
    if not _source_common_binding_matches(value, binding):
        _fail("STRICT_REMOTE_ACK_WRITER_RESPONSE_BINDING_MISMATCH")
    target, target_value = _lsn(value.target_acknowledged_wal_lsn, code="STRICT_REMOTE_ACK_WRITER_RESPONSE_INVALID")
    blob, blob_value = _lsn(value.blob_object_frontier_wal_lsn, code="STRICT_REMOTE_ACK_WRITER_RESPONSE_INVALID")
    if (
        target != binding.target_acknowledged_wal_lsn
        or blob != binding.blob_object_frontier_wal_lsn
        or target_value < binding.baseline_wal_lsn_value
        or blob_value < target_value
        or value.writer_response_mode != PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_WRITER_RESPONSE_MODE
        or value.receiver_response_source != PHYSICAL_FULL_MATRIX_STRICT_REMOTE_ACK_RECEIVER_RESPONSE_SOURCE
        or value.durable_commit_coupled is not True
        or value.fences_writes_when_ack_unavailable is not True
    ):
        _fail("STRICT_REMOTE_ACK_WRITER_RESPONSE_POLICY_MISMATCH")
    return value


def verify_physical_full_matrix_strict_remote_ack_writer_response(
    value: object,
    *,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS,
) -> VerifiedPhysicalFullMatrixStrictRemoteAckWriterResponse:
    """Verify the mandatory local observation for the strict writer response gap."""

    facts = _normalise_binding(binding)
    observed_now = _utc(now, code="STRICT_REMOTE_ACK_WRITER_RESPONSE_INVALID")
    maximum = _maximum_evidence_age(
        maximum_evidence_age_seconds,
        code="STRICT_REMOTE_ACK_WRITER_RESPONSE_INVALID",
    )
    observation = _strict_writer_response_facts(
        value,
        binding=facts,
        now=observed_now,
        maximum_evidence_age_seconds=maximum,
    )
    result = VerifiedPhysicalFullMatrixStrictRemoteAckWriterResponse(observation=observation)
    object.__setattr__(result, "_capability", _VERIFIED_STRICT_ACK_WRITER_RESPONSE_CAPABILITY)
    return result


def require_verified_physical_full_matrix_strict_remote_ack_writer_response(
    value: object,
    *,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS,
) -> VerifiedPhysicalFullMatrixStrictRemoteAckWriterResponse:
    """Revalidate strict writer-response evidence without touching a writer."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixStrictRemoteAckWriterResponse
        or value._capability is not _VERIFIED_STRICT_ACK_WRITER_RESPONSE_CAPABILITY
    ):
        _fail("VERIFIED_STRICT_REMOTE_ACK_WRITER_RESPONSE_REQUIRED")
    normalized = verify_physical_full_matrix_strict_remote_ack_writer_response(
        value.observation,
        binding=binding,
        now=now,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
    )
    if normalized.observation != value.observation:
        _fail("VERIFIED_STRICT_REMOTE_ACK_WRITER_RESPONSE_TAMPERED")
    return value


def _external_effect_reconciliation_facts(
    value: object,
    *,
    binding: _BindingFacts,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> ExternalEffectExecutionAuthorization:
    """Normalize a supplied external-effect decision without reading its file.

    The owning gate's file-safe loader and worker-time live-term validation are
    intentionally outside this oracle.  Requiring a parsed typed result here
    avoids accidentally accepting a path, raw JSON, or worker authorization
    as an execution capability.
    """

    if type(value) is not ExternalEffectExecutionAuthorization:
        _fail("EXTERNAL_EFFECT_RECONCILIATION_INVALID")
    try:
        mapping = external_effect_execution_authorization_mapping(value)
        normalized = parse_external_effect_execution_authorization(mapping)
    except ExternalEffectExecutionGateError as exc:
        raise PhysicalFullMatrixCampaignReadinessError(
            "EXTERNAL_EFFECT_RECONCILIATION_INVALID"
        ) from exc
    if normalized != value:
        _fail("EXTERNAL_EFFECT_RECONCILIATION_TAMPERED")
    if (
        normalized.holder_site != binding.source_site
        or type(normalized.writer_epoch) is not int
        or normalized.writer_epoch != binding.writer_epoch
        or normalized.writer_lease_id != binding.writer_lease_id
        or normalized.witness_transition_id != binding.witness_transition_id
        or normalized.reconciliation_decision
        != RECONCILIATION_DECISION_COMPLETE_NO_RESEND
        or set(normalized.authorized_scopes) != EXTERNAL_EFFECT_EXECUTION_SCOPES
        or len(normalized.authorized_scopes) != len(EXTERNAL_EFFECT_EXECUTION_SCOPES)
        or normalized.reconciliation_evidence_sha256 == "0" * 64
    ):
        _fail("EXTERNAL_EFFECT_RECONCILIATION_BINDING_MISMATCH")
    _fresh(
        normalized.issued_at,
        now=now,
        maximum_age_seconds=maximum_evidence_age_seconds,
        code="EXTERNAL_EFFECT_RECONCILIATION_INVALID",
    )
    if (
        normalized.writer_term_expires_at <= now
        or normalized.expires_at <= now
        or normalized.reconciliation_completed_at > normalized.issued_at
    ):
        _fail("EXTERNAL_EFFECT_RECONCILIATION_STALE_OR_EXPIRED")
    return normalized


def verify_physical_full_matrix_external_effect_reconciliation(
    value: object,
    *,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS,
) -> VerifiedPhysicalFullMatrixExternalEffectReconciliation:
    """Bind a typed reconciliation decision without authorizing a worker."""

    facts = _normalise_binding(binding)
    observed_now = _utc(now, code="EXTERNAL_EFFECT_RECONCILIATION_INVALID")
    maximum = _maximum_evidence_age(
        maximum_evidence_age_seconds,
        code="EXTERNAL_EFFECT_RECONCILIATION_INVALID",
    )
    authorization = _external_effect_reconciliation_facts(
        value,
        binding=facts,
        now=observed_now,
        maximum_evidence_age_seconds=maximum,
    )
    result = VerifiedPhysicalFullMatrixExternalEffectReconciliation(
        authorization=authorization
    )
    object.__setattr__(result, "_capability", _VERIFIED_EXTERNAL_EFFECT_CAPABILITY)
    return result


def require_verified_physical_full_matrix_external_effect_reconciliation(
    value: object,
    *,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS,
) -> VerifiedPhysicalFullMatrixExternalEffectReconciliation:
    """Recheck injected reconciliation evidence without file or worker I/O."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixExternalEffectReconciliation
        or value._capability is not _VERIFIED_EXTERNAL_EFFECT_CAPABILITY
    ):
        _fail("VERIFIED_EXTERNAL_EFFECT_RECONCILIATION_REQUIRED")
    normalized = verify_physical_full_matrix_external_effect_reconciliation(
        value.authorization,
        binding=binding,
        now=now,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
    )
    if normalized.authorization != value.authorization:
        _fail("VERIFIED_EXTERNAL_EFFECT_RECONCILIATION_TAMPERED")
    return value


def _is_exact_nominal_type(value: object, *, module: str, name: str) -> bool:
    """Recognize a typed service result without importing application settings.

    The P0 service implementation imports the configured database module.  A
    readiness oracle must remain importable in a clean/offline environment, so
    it cannot import that service merely to identify an already injected
    result.  The following fields are still checked strictly before use; this
    nominal check rejects ordinary look-alike dataclasses at the boundary.
    """

    # A genuine result necessarily came from a module that is already loaded
    # in this process.  Looking only in ``sys.modules`` avoids importing the
    # application database settings merely for an ``isinstance`` check while
    # still requiring the exact real class rather than a look-alike whose
    # ``__module__``/``__qualname__`` attributes were forged.
    owner = sys.modules.get(module)
    expected = None if owner is None else getattr(owner, name, None)
    return isinstance(expected, type) and type(value) is expected


def _p0_auth_upload_facts(
    value: object,
    *,
    binding: _BindingFacts,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> object:
    """Check the selected transaction participants as an injected result only.

    There is no durable database read in this driver.  Consequently a valid
    result says only that matching participants were supplied, never that the
    caller committed their transaction.  A later coordinator must make that
    durable confirmation at its own database boundary.
    """

    if not _is_exact_nominal_type(
        value,
        module="core.services.promotion_continuity_participants",
        name="PromotionContinuityParticipantsResult",
    ):
        _fail("P0_AUTH_UPLOAD_RESULT_INVALID")
    auth = value.auth
    uploads = value.uploads
    if (
        not _is_exact_nominal_type(
            auth,
            module="core.services.promotion_session_invalidation_service",
            name="PromotionSessionInvalidationResult",
        )
        or not _is_exact_nominal_type(
            uploads,
            module="core.services.promotion_upload_cleanup_service",
            name="PromotionUploadCleanupResult",
        )
    ):
        _fail("P0_AUTH_UPLOAD_RESULT_INVALID")
    for participant in (auth, uploads):
        if (
            type(participant.operation_id) is not UUID
            or participant.operation_id.int == 0
            or participant.writer_site != binding.source_site
            or type(participant.writer_epoch) is not int
            or participant.writer_epoch != binding.writer_epoch
            or not isinstance(participant.writer_lease_id, str)
            or participant.writer_lease_id != binding.writer_lease_id
            or not isinstance(participant.witness_transition_id, str)
            or participant.witness_transition_id != binding.witness_transition_id
            or participant.applied is not True
        ):
            _fail("P0_AUTH_UPLOAD_RESULT_BINDING_MISMATCH")
    if (
        auth.operation_id != binding.p0_operation_id
        or uploads.operation_id != binding.p0_operation_id
        or auth.operation_id != uploads.operation_id
        or auth.writer_site != uploads.writer_site
        or auth.writer_epoch != uploads.writer_epoch
        or auth.writer_lease_id != uploads.writer_lease_id
        or auth.witness_transition_id != uploads.witness_transition_id
    ):
        _fail("P0_AUTH_UPLOAD_RESULT_BINDING_MISMATCH")
    auth_cutover = _fresh(
        auth.cutover_at,
        now=now,
        maximum_age_seconds=maximum_evidence_age_seconds,
        code="P0_AUTH_UPLOAD_RESULT_INVALID",
    )
    upload_cutover = _fresh(
        uploads.cutover_at,
        now=now,
        maximum_age_seconds=maximum_evidence_age_seconds,
        code="P0_AUTH_UPLOAD_RESULT_INVALID",
    )
    if auth_cutover != upload_cutover:
        _fail("P0_AUTH_UPLOAD_RESULT_BINDING_MISMATCH")
    if (
        type(auth.minimum_token_iat) is not int
        or auth.minimum_token_iat < 0
        or type(auth.invalidated_sessions) is not int
        or auth.invalidated_sessions < 0
        or type(auth.expired_login_requests) is not int
        or auth.expired_login_requests < 0
        or type(auth.cancelled_recovery_requests) is not int
        or auth.cancelled_recovery_requests < 0
        or type(uploads.cancelled_session_ids) is not tuple
        or type(uploads.cancelled_batch_ids) is not tuple
        or any(not isinstance(item, str) or not item for item in uploads.cancelled_session_ids)
        or any(not isinstance(item, str) or not item for item in uploads.cancelled_batch_ids)
    ):
        _fail("P0_AUTH_UPLOAD_RESULT_INVALID")
    return value


def _bundle_object_versions(
    bundle: VerifiedPhysicalWalObjectStorageBundle,
) -> tuple[tuple[str, str], ...]:
    """Project exact immutable object IDs in the manifest bundle's order."""

    result: list[tuple[str, str]] = [
        (
            bundle.baseline.base_backup_object.object_key,
            bundle.baseline.base_backup_object.version_id,
        )
    ]
    for manifest in bundle.wal_manifests:
        result.extend((segment.object.object_key, segment.object.version_id) for segment in manifest.segments)
    result.extend(
        (shard.object.object_key, shard.object.version_id)
        for shard in bundle.blob_frontier.inventory_shards
    )
    return tuple(result)


def _bundle_facts(
    value: object,
    *,
    binding: _BindingFacts,
) -> VerifiedPhysicalWalObjectStorageBundle:
    try:
        bundle = require_verified_physical_wal_object_storage_bundle(value)
    except (PhysicalWalObjectManifestError, AttributeError, TypeError) as exc:
        raise PhysicalFullMatrixCampaignReadinessError("PHYSICAL_WAL_BUNDLE_INVALID") from exc
    baseline = bundle.baseline
    blob = bundle.blob_frontier
    try:
        terminal, terminal_value = _lsn(bundle.terminal_wal_lsn, code="PHYSICAL_WAL_BUNDLE_INVALID")
        if (
            baseline.source_site != binding.source_site
            or baseline.destination_site != binding.destination_site
            or baseline.campaign_id != binding.campaign_id
            or baseline.release_sha != binding.release_sha
            or baseline.baseline_generation_id != binding.baseline_generation_id
            or baseline.manifest_sha256 != binding.baseline_manifest_sha256
            or baseline.baseline_wal_lsn != binding.baseline_wal_lsn
            or type(baseline.timeline_id) is not int
            or baseline.timeline_id != binding.timeline_id
            or baseline.writer_term.epoch != binding.writer_epoch
            or baseline.writer_term.lease_id != binding.writer_lease_id
            or baseline.writer_term.witnessed_term_proof_sha256
            != binding.witnessed_term_proof_sha256
            or baseline.base_backup_object.age_recipient != binding.destination_age_recipient
            or blob.source_site != binding.source_site
            or blob.destination_site != binding.destination_site
            or blob.campaign_id != binding.campaign_id
            or blob.release_sha != binding.release_sha
            or blob.baseline_generation_id != binding.baseline_generation_id
            or blob.baseline_manifest_sha256 != binding.baseline_manifest_sha256
            or type(blob.timeline_id) is not int
            or blob.timeline_id != binding.timeline_id
            or blob.writer_term.epoch != binding.writer_epoch
            or blob.writer_term.lease_id != binding.writer_lease_id
            or blob.writer_term.witnessed_term_proof_sha256
            != binding.witnessed_term_proof_sha256
            or blob.objects_complete is not True
            or terminal_value < binding.target_acknowledged_wal_lsn_value
        ):
            _fail("PHYSICAL_WAL_BUNDLE_BINDING_MISMATCH")
        blob_frontier, blob_frontier_value = _lsn(
            blob.blob_object_frontier_wal_lsn,
            code="PHYSICAL_WAL_BUNDLE_INVALID",
        )
        if (
            blob_frontier != binding.blob_object_frontier_wal_lsn
            or blob_frontier_value < binding.target_acknowledged_wal_lsn_value
            or terminal != bundle.terminal_wal_lsn
            or not bundle.manifest_sha256es
            or len(set(bundle.manifest_sha256es)) != len(bundle.manifest_sha256es)
            or len(set(_bundle_object_versions(bundle))) != len(_bundle_object_versions(bundle))
        ):
            _fail("PHYSICAL_WAL_BUNDLE_BINDING_MISMATCH")
    except (AttributeError, PhysicalFullMatrixCampaignReadinessError):
        raise
    except (TypeError, ValueError) as exc:
        raise PhysicalFullMatrixCampaignReadinessError("PHYSICAL_WAL_BUNDLE_INVALID") from exc
    return bundle


def _v2_chunked_recovery_evidence_facts(
    value: object,
    *,
    binding: _BindingFacts,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> VerifiedPhysicalFullMatrixV2RecoveryEvidence:
    """Recheck the non-authorizing V2 recovery/coverage bridge.

    This slot proves one exact chunked-base-backup recovery target only.  It
    cannot be adapted into the V1 request/receipt/ledger writer-ack chain and
    cannot authorize recovery, promotion, routing, or execution.  In
    particular, the campaign's Blob frontier must equal this exact target:
    accepting a later frontier would claim coverage the bridge did not check.
    """

    try:
        evidence = require_verified_physical_full_matrix_v2_recovery_evidence(
            value,
            now=now,
        )
        if type(evidence) is not VerifiedPhysicalFullMatrixV2RecoveryEvidence:
            _fail("V2_CHUNKED_RECOVERY_EVIDENCE_INVALID")
        transfer = evidence.transfer_binding
        if type(transfer) is not PhysicalWalChunkedBaseBackupBinding:
            _fail("V2_CHUNKED_RECOVERY_EVIDENCE_INVALID")
        target_lsn, target_lsn_value = _lsn(
            evidence.target_replay_lsn,
            code="V2_CHUNKED_RECOVERY_EVIDENCE_INVALID",
        )
        baseline_lsn, baseline_lsn_value = _lsn(
            evidence.baseline_wal_lsn,
            code="V2_CHUNKED_RECOVERY_EVIDENCE_INVALID",
        )
        _fresh(
            evidence.observed_at,
            now=now,
            maximum_age_seconds=maximum_evidence_age_seconds,
            code="V2_CHUNKED_RECOVERY_EVIDENCE_INVALID",
        )
        if (
            transfer.source_site != binding.source_site
            or transfer.destination_site != binding.destination_site
            or transfer.campaign_id != binding.campaign_id
            or transfer.release_sha != binding.release_sha
            or transfer.route_commitment_sha256 != binding.route_binding_sha256
            or transfer.destination_age_recipient != binding.destination_age_recipient
            or transfer.writer_term.writer_holder_site != binding.source_site
            or transfer.writer_term.writer_epoch != binding.writer_epoch
            or transfer.writer_term.writer_lease_id != binding.writer_lease_id
            or transfer.writer_term.witnessed_term_proof_sha256
            != binding.witnessed_term_proof_sha256
            or evidence.stream_generation_id != binding.stream_generation_id
            or evidence.route_commitment_sha256 != binding.route_binding_sha256
            or evidence.manifest_sha256 != binding.baseline_manifest_sha256
            or evidence.baseline_generation_id != binding.baseline_generation_id
            or baseline_lsn != binding.baseline_wal_lsn
            or baseline_lsn_value != binding.baseline_wal_lsn_value
            or evidence.timeline_id != binding.timeline_id
            or evidence.witness_transition_id != binding.witness_transition_id
            or target_lsn != binding.target_acknowledged_wal_lsn
            or target_lsn_value != binding.target_acknowledged_wal_lsn_value
            # The V2 bridge covers exactly ``target_lsn``.  A campaign that
            # claims a later Blob frontier needs a later V2 coverage proof.
            or binding.blob_object_frontier_wal_lsn
            != binding.target_acknowledged_wal_lsn
            or binding.blob_object_frontier_wal_lsn_value
            != binding.target_acknowledged_wal_lsn_value
            or evidence.stage_receipt_sha256 != binding.recovery_stage_receipt_sha256
            or evidence.recovery_authorized is not False
            or evidence.promotion_authorized is not False
            or evidence.execution_authorized is not False
        ):
            _fail("V2_CHUNKED_RECOVERY_EVIDENCE_BINDING_MISMATCH")
    except PhysicalFullMatrixCampaignReadinessError:
        raise
    except (
        PhysicalFullMatrixV2RecoveryEvidenceError,
        AttributeError,
        TypeError,
        ValueError,
    ) as exc:
        # Preserve the readiness boundary's single diagnostic category rather
        # than exposing upstream implementation details in the public report.
        raise PhysicalFullMatrixCampaignReadinessError(
            "V2_CHUNKED_RECOVERY_EVIDENCE_INVALID"
        ) from exc
    return evidence


def _recovery_result_facts(
    value: object,
    *,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    binding: _BindingFacts,
) -> PhysicalPostgresRecoveryPreflightResult:
    if type(value) is not PhysicalPostgresRecoveryPreflightResult:
        _fail("PHYSICAL_WAL_RECOVERY_OBSERVATION_INVALID")
    try:
        replay, replay_value = _lsn(value.replay_lsn, code="PHYSICAL_WAL_RECOVERY_OBSERVATION_INVALID")
        terminal, terminal_value = _lsn(value.terminal_wal_lsn, code="PHYSICAL_WAL_RECOVERY_OBSERVATION_INVALID")
        expected_objects = _bundle_object_versions(bundle)
        if (
            value.schema != PHYSICAL_POSTGRES_RECOVERY_PREFLIGHT_SCHEMA
            or value.status != PHYSICAL_POSTGRES_RECOVERY_STATUS_REPLAY_EVIDENCE_OBSERVED
            or value.reason_codes != ()
            or value.local_standby_site != binding.destination_site
            or value.source_site != binding.source_site
            or value.destination_site != binding.destination_site
            or value.stage_bundle_id != binding.recovery_stage_bundle_id
            or value.stage_receipt_sha256 != binding.recovery_stage_receipt_sha256
            or value.route_binding_sha256 != binding.route_binding_sha256
            or value.manifest_sha256es != bundle.manifest_sha256es
            or value.object_versions != expected_objects
            or terminal != bundle.terminal_wal_lsn
            or terminal_value < binding.target_acknowledged_wal_lsn_value
            or replay_value < binding.target_acknowledged_wal_lsn_value
            or replay != value.replay_lsn
            or not isinstance(value.evidence_sha256, str)
            or _sha256(value.evidence_sha256, code="PHYSICAL_WAL_RECOVERY_OBSERVATION_INVALID")
            != value.evidence_sha256
        ):
            _fail("PHYSICAL_WAL_RECOVERY_OBSERVATION_BINDING_MISMATCH")
    except (AttributeError, PhysicalFullMatrixCampaignReadinessError):
        raise
    return value


def _recovery_observation_facts(
    value: object,
    *,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    binding: _BindingFacts,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> VerifiedPhysicalFullMatrixRecoveryObservation:
    if (
        type(value) is not VerifiedPhysicalFullMatrixRecoveryObservation
        or value._capability is not _VERIFIED_RECOVERY_CAPABILITY
    ):
        _fail("VERIFIED_PHYSICAL_WAL_RECOVERY_REQUIRED")
    normalized = verify_physical_full_matrix_recovery_observation(
        value.observation,
        bundle=bundle,
        binding=_binding_from_facts(binding),
        now=now,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
    )
    if normalized.observation != value.observation:
        _fail("VERIFIED_PHYSICAL_WAL_RECOVERY_TAMPERED")
    return value


def _binding_from_facts(facts: _BindingFacts) -> PhysicalFullMatrixCampaignBinding:
    """Recreate the immutable public binding for nested verifier rechecks."""

    return PhysicalFullMatrixCampaignBinding(
        campaign_id=facts.campaign_id,
        release_sha=facts.release_sha,
        schema_revision=facts.schema_revision,
        source_site=facts.source_site,
        destination_site=facts.destination_site,
        baseline_generation_id=facts.baseline_generation_id,
        baseline_manifest_sha256=facts.baseline_manifest_sha256,
        baseline_wal_lsn=facts.baseline_wal_lsn,
        timeline_id=facts.timeline_id,
        stream_generation_id=facts.stream_generation_id,
        destination_age_recipient=facts.destination_age_recipient,
        route_binding_sha256=facts.route_binding_sha256,
        writer_epoch=facts.writer_epoch,
        writer_lease_id=facts.writer_lease_id,
        witness_transition_id=facts.witness_transition_id,
        witnessed_term_proof_sha256=facts.witnessed_term_proof_sha256,
        target_acknowledged_wal_lsn=facts.target_acknowledged_wal_lsn,
        blob_object_frontier_wal_lsn=facts.blob_object_frontier_wal_lsn,
        recovery_stage_bundle_id=facts.recovery_stage_bundle_id,
        recovery_stage_receipt_sha256=facts.recovery_stage_receipt_sha256,
        deployment_operation_id=facts.deployment_operation_id,
        deployment_manifest_sha256=facts.deployment_manifest_sha256,
        p0_operation_id=facts.p0_operation_id,
    )


def verify_physical_full_matrix_recovery_observation(
    value: object,
    *,
    bundle: object,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS,
) -> VerifiedPhysicalFullMatrixRecoveryObservation:
    """Bind a fresh recovery result to a physical bundle without PostgreSQL I/O."""

    if type(value) is not PhysicalFullMatrixRecoveryObservation:
        _fail("PHYSICAL_WAL_RECOVERY_OBSERVATION_INVALID")
    if value.schema != PHYSICAL_FULL_MATRIX_RECOVERY_OBSERVATION_SCHEMA or value.status != _OBSERVATION_STATUS:
        _fail("PHYSICAL_WAL_RECOVERY_OBSERVATION_INVALID")
    facts = _normalise_binding(binding)
    observed_now = _utc(now, code="PHYSICAL_WAL_RECOVERY_OBSERVATION_INVALID")
    maximum = _maximum_evidence_age(
        maximum_evidence_age_seconds,
        code="PHYSICAL_WAL_RECOVERY_OBSERVATION_INVALID",
    )
    _fresh(
        value.observed_at,
        now=observed_now,
        maximum_age_seconds=maximum,
        code="PHYSICAL_WAL_RECOVERY_OBSERVATION_INVALID",
    )
    evidence_sha = _sha256(
        value.recovery_evidence_sha256,
        code="PHYSICAL_WAL_RECOVERY_OBSERVATION_INVALID",
    )
    verified_bundle = _bundle_facts(bundle, binding=facts)
    result = _recovery_result_facts(
        value.recovery_result,
        bundle=verified_bundle,
        binding=facts,
    )
    if result.evidence_sha256 != evidence_sha:
        _fail("PHYSICAL_WAL_RECOVERY_OBSERVATION_BINDING_MISMATCH")
    verified = VerifiedPhysicalFullMatrixRecoveryObservation(observation=value)
    object.__setattr__(verified, "_capability", _VERIFIED_RECOVERY_CAPABILITY)
    return verified


def require_verified_physical_full_matrix_recovery_observation(
    value: object,
    *,
    bundle: object,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int = DEFAULT_PHYSICAL_FULL_MATRIX_MAX_EVIDENCE_AGE_SECONDS,
) -> VerifiedPhysicalFullMatrixRecoveryObservation:
    """Revalidate a fresh recovery envelope from its typed result."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixRecoveryObservation
        or value._capability is not _VERIFIED_RECOVERY_CAPABILITY
    ):
        _fail("VERIFIED_PHYSICAL_WAL_RECOVERY_REQUIRED")
    normalized = verify_physical_full_matrix_recovery_observation(
        value.observation,
        bundle=bundle,
        binding=binding,
        now=now,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
    )
    if normalized.observation != value.observation:
        _fail("VERIFIED_PHYSICAL_WAL_RECOVERY_TAMPERED")
    return value


def _remote_ack_facts(
    value: object,
    *,
    bundle: VerifiedPhysicalWalObjectStorageBundle,
    binding: _BindingFacts,
    now: datetime,
) -> tuple[VerifiedPhysicalWalRemoteAckEvidence, object]:
    """Recheck exact source request, signed receipt, and typed IR recovery.

    This remains a local evidence comparison.  It does not issue a receipt,
    read the IR ledger, or make an FI write acknowledgement.
    """

    try:
        remote = require_verified_physical_wal_remote_ack_evidence(value, now=now)
        remote_binding = remote.binding
        if type(remote_binding) is not PhysicalWalRemoteAckBinding:
            _fail("REMOTE_ACK_EVIDENCE_INVALID")
        object_versions = tuple(
            (item.object_key, item.version_id) for item in remote_binding.object_versions
        )
        if (
            remote_binding.source_site != binding.source_site
            or remote_binding.destination_site != binding.destination_site
            or remote_binding.destination_age_recipient != binding.destination_age_recipient
            or remote_binding.campaign_id != binding.campaign_id
            or remote_binding.release_sha != binding.release_sha
            or remote_binding.stream_generation_id != binding.stream_generation_id
            or remote_binding.baseline_generation_id != binding.baseline_generation_id
            or remote_binding.baseline_manifest_sha256 != binding.baseline_manifest_sha256
            or remote_binding.writer_term.writer_holder_site != binding.source_site
            or type(remote_binding.writer_term.writer_epoch) is not int
            or remote_binding.writer_term.writer_epoch != binding.writer_epoch
            or remote_binding.writer_term.writer_lease_id != binding.writer_lease_id
            or remote_binding.writer_term.witnessed_term_proof_sha256
            != binding.witnessed_term_proof_sha256
            or remote_binding.target_acknowledged_wal_lsn
            != binding.target_acknowledged_wal_lsn
            or remote_binding.blob_object_frontier_wal_lsn
            != binding.blob_object_frontier_wal_lsn
            or remote_binding.objects_complete is not True
            or remote_binding.manifest_sha256es != bundle.manifest_sha256es
            or object_versions != _bundle_object_versions(bundle)
        ):
            _fail("REMOTE_ACK_EVIDENCE_BINDING_MISMATCH")
        request = verify_physical_wal_remote_ack_request(
            source_request=remote.source_request,
            expected_binding=remote_binding,
            expected_source_public_key=remote.source_public_key,
            now=now,
        )
    except (PhysicalWalRemoteAckError, AttributeError, TypeError) as exc:
        raise PhysicalFullMatrixCampaignReadinessError("REMOTE_ACK_EVIDENCE_INVALID") from exc
    return remote, request  # The caller binds recovery as a distinct required slot.


def _remote_ack_receiver_recovery_facts(
    value: object,
    *,
    request: object,
    binding: _BindingFacts,
    now: datetime,
) -> VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence:
    try:
        recovery = require_verified_physical_wal_remote_ack_receiver_recovery_evidence(
            value,
            source_request=request,
            now=now,
        )
        evidence = recovery.evidence
        replay, replay_value = _lsn(
            evidence.replay_lsn,
            code="REMOTE_ACK_RECEIVER_RECOVERY_INVALID",
        )
        if (
            evidence.source_site != binding.source_site
            or evidence.destination_site != binding.destination_site
            or evidence.receiver_site != binding.destination_site
            or replay != evidence.replay_lsn
            or replay_value < binding.target_acknowledged_wal_lsn_value
            or evidence.in_recovery is not True
            or evidence.role != "standby"
        ):
            _fail("REMOTE_ACK_RECEIVER_RECOVERY_BINDING_MISMATCH")
    except (
        PhysicalWalRemoteAckReceiverLedgerError,
        PhysicalWalRemoteAckError,
        AttributeError,
        TypeError,
    ) as exc:
        raise PhysicalFullMatrixCampaignReadinessError(
            "REMOTE_ACK_RECEIVER_RECOVERY_INVALID"
        ) from exc
    return recovery


def _remote_ack_durable_ledger_facts(
    value: object,
    *,
    remote: VerifiedPhysicalWalRemoteAckEvidence,
    recovery: VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence,
    binding: _BindingFacts,
) -> PhysicalWalRemoteAckReceiverLedgerResult:
    """Bind a ledger result identity without opening its ledger path."""

    if type(value) is not PhysicalWalRemoteAckReceiverLedgerResult:
        _fail("REMOTE_ACK_DURABLE_LEDGER_INVALID")
    try:
        replay, replay_value = _lsn(value.receiver_replay_lsn, code="REMOTE_ACK_DURABLE_LEDGER_INVALID")
        expected_request_sha = hashlib.sha256(remote.source_request).hexdigest()
        expected_receipt_sha = hashlib.sha256(remote.destination_receipt).hexdigest()
        recovery_evidence = recovery.evidence
        if (
            value.destination_receipt != remote.destination_receipt
            or value.destination_receipt_sha256 != expected_receipt_sha
            or value.source_request_sha256 != expected_request_sha
            or value.receipt_id != remote.receipt_id
            or value.receipt_nonce != remote.receipt_nonce
            or _utc(value.acknowledged_at, code="REMOTE_ACK_DURABLE_LEDGER_INVALID")
            != remote.acknowledged_at
            or value.receiver_recovery_evidence_sha256
            != recovery_evidence.receiver_recovery_evidence_sha256
            or replay != recovery_evidence.replay_lsn
            or replay_value < binding.target_acknowledged_wal_lsn_value
            or type(value.ledger_path) is not Path
            or not value.ledger_path.is_absolute()
            or type(value.idempotent) is not bool
        ):
            _fail("REMOTE_ACK_DURABLE_LEDGER_BINDING_MISMATCH")
    except (AttributeError, PhysicalFullMatrixCampaignReadinessError):
        raise
    return value


def _blob_promotion_facts(
    *,
    evidence_value: object,
    storage_binding_value: object,
    config_value: object,
    binding: _BindingFacts,
    now: datetime,
) -> VerifiedPhysicalBlobReceiverPromotionEvidence:
    """Require current-pinned receiver v2 Blob promotion evidence.

    The parent Blob boundary owns all mapping-receipt signature and freshness
    rechecks.  The oracle intentionally supplies its mandatory config every
    time; no historical two-argument compatibility path exists.
    """

    if type(config_value) is not PhysicalBlobReceiverPromotionEvidenceConfig:
        _fail("BLOB_PROMOTION_EVIDENCE_INVALID")
    try:
        storage_binding = require_verified_physical_blob_object_storage_binding(
            storage_binding_value,
            now=now,
        )
        evidence = require_verified_physical_blob_receiver_promotion_evidence(
            evidence_value,
            config=config_value,
            verified_binding=storage_binding,
            now=now,
        )
        if type(evidence) is not VerifiedPhysicalBlobReceiverPromotionEvidence:
            _fail("BLOB_PROMOTION_EVIDENCE_INVALID")
        eligible, eligible_value = _lsn(
            evidence.mapping_eligible_replay_wal_lsn,
            code="BLOB_PROMOTION_EVIDENCE_INVALID",
        )
        if (
            evidence.source_site != binding.source_site
            or evidence.destination_site != binding.destination_site
            or evidence.campaign_id != binding.campaign_id
            or evidence.release_sha != binding.release_sha
            or evidence.baseline_generation_id != binding.baseline_generation_id
            or evidence.baseline_manifest_sha256 != binding.baseline_manifest_sha256
            or evidence.baseline_wal_lsn != binding.baseline_wal_lsn
            or evidence.route_binding_sha256 != binding.route_binding_sha256
            or type(evidence.writer_epoch) is not int
            or evidence.writer_epoch != binding.writer_epoch
            or evidence.writer_lease_id != binding.writer_lease_id
            or evidence.witnessed_term_proof_sha256
            != binding.witnessed_term_proof_sha256
            or evidence.destination_age_recipient != binding.destination_age_recipient
            or type(evidence.timeline_id) is not int
            or evidence.timeline_id != binding.timeline_id
            or eligible != binding.baseline_wal_lsn
            or eligible_value != binding.baseline_wal_lsn_value
        ):
            _fail("BLOB_PROMOTION_EVIDENCE_BINDING_MISMATCH")
    except (
        PhysicalBlobObjectStorageUploaderError,
        PhysicalBlobReceiverPromotionEvidenceError,
        AttributeError,
        TypeError,
    ) as exc:
        raise PhysicalFullMatrixCampaignReadinessError("BLOB_PROMOTION_EVIDENCE_INVALID") from exc
    return evidence


def _witness_term_facts(
    value: object,
    *,
    binding: _BindingFacts,
    now: datetime,
) -> VerifiedObjectDeltaRoleMatrixWitnessedTerm:
    try:
        term = require_live_object_delta_role_matrix_witnessed_term(value, now=now)
        if (
            term.holder_site != binding.source_site
            or type(term.writer_epoch) is not int
            or term.writer_epoch != binding.writer_epoch
            or term.writer_lease_id != binding.writer_lease_id
            or term.witness_transition_id != binding.witness_transition_id
            or term.proof_sha256 != binding.witnessed_term_proof_sha256
        ):
            _fail("CURRENT_WITNESS_TERM_BINDING_MISMATCH")
    except (ObjectDeltaRoleMatrixRolloverError, AttributeError, TypeError) as exc:
        raise PhysicalFullMatrixCampaignReadinessError("CURRENT_WITNESS_TERM_INVALID") from exc
    return term


def _role_activation_facts(
    value: object,
    *,
    binding: _BindingFacts,
    now: datetime,
) -> VerifiedObjectDeltaRoleMatrixActivation:
    try:
        activation = require_live_object_delta_role_matrix_activation(value, now=now)
        source_role = project_active_object_delta_role_matrix_role(
            activation,
            site=binding.source_site,
            now=now,
        )
        destination_role = project_active_object_delta_role_matrix_role(
            activation,
            site=binding.destination_site,
            now=now,
        )
        if (
            source_role.site != binding.source_site
            or source_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_WRITER_SOURCE
            or destination_role.site != binding.destination_site
            or destination_role.role != OBJECT_DELTA_ROLE_MATRIX_ROLE_STANDBY_RECEIVER
        ):
            _fail("CURRENT_ROLE_ACTIVATION_BINDING_MISMATCH")
    except (ObjectDeltaRoleMatrixRolloverError, AttributeError, TypeError) as exc:
        raise PhysicalFullMatrixCampaignReadinessError("CURRENT_ROLE_ACTIVATION_INVALID") from exc
    return activation


_REASON_ORDER = (
    "driver-disabled",
    "invalid-campaign-binding",
    "invalid-assessment-clock",
    "invalid-campaign-inputs",
    "legacy-runner-artifact-rejected",
    "v1-single-object-base-backup-activation-fenced",
    "missing-physical-wal-recovery-observation",
    "physical-wal-recovery-observation-mismatch",
    "missing-physical-wal-bundle",
    "physical-wal-bundle-mismatch",
    "missing-remote-ack-evidence",
    "remote-ack-evidence-mismatch",
    "missing-remote-ack-receiver-recovery",
    "remote-ack-receiver-recovery-mismatch",
    "missing-remote-ack-durable-ledger",
    "remote-ack-durable-ledger-mismatch",
    "missing-strict-remote-ack-writer-response",
    "strict-remote-ack-writer-response-mismatch",
    "missing-arvan-object-storage-immutability-preflight",
    "arvan-object-storage-immutability-preflight-mismatch",
    "legacy-arvan-object-storage-immutability-preflight-rejected",
    "missing-four-role-arvan-object-storage-immutability-preflight",
    "four-role-arvan-object-storage-immutability-preflight-mismatch",
    "missing-arvan-object-storage-failback-preflight",
    "arvan-object-storage-failback-preflight-mismatch",
    "missing-blob-promotion-evidence",
    "blob-promotion-evidence-mismatch",
    "missing-current-witness-term",
    "witness-term-mismatch",
    "missing-current-role-activation",
    "role-activation-mismatch",
    "missing-deployment-preflight-posture",
    "deployment-preflight-posture-mismatch",
    "missing-p0-auth-upload-result",
    "p0-auth-upload-result-mismatch",
    "missing-external-effect-reconciliation-decision",
    "external-effect-reconciliation-mismatch",
    "missing-source-write-fence-recovery-route",
    "source-write-fence-recovery-route-mismatch",
)
_REASON_ORDER_INDEX = {code: index for index, code in enumerate(_REASON_ORDER)}


def _legacy_artifacts_present(value: object) -> bool:
    """Return true for every nonempty historical artifact without consuming it."""

    if value is None:
        return False
    if isinstance(value, (str, bytes, bytearray)):
        return bool(value)
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Collection):
        return bool(value)
    # A generator/path/object could hide a legacy plan.  It is not a valid
    # empty collection, so fail closed without iterating or deserializing it.
    return True


def _ordered_reasons(reasons: set[str]) -> tuple[str, ...]:
    unknown = reasons.difference(_REASON_ORDER_INDEX)
    if unknown:
        return tuple(sorted(reasons, key=lambda item: (_REASON_ORDER_INDEX.get(item, 10_000), item)))
    return tuple(sorted(reasons, key=_REASON_ORDER_INDEX.__getitem__))


def _readiness_report(
    *,
    facts: _BindingFacts | None,
    reasons: set[str],
    observed_slots: set[str],
) -> PhysicalFullMatrixCampaignReadiness:
    ordered_reasons = _ordered_reasons(reasons)
    status = (
        PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
        if not ordered_reasons
        else PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_BLOCKED
    )
    return PhysicalFullMatrixCampaignReadiness(
        schema=PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA,
        status=status,
        reason_codes=ordered_reasons,
        campaign_id=None if facts is None else facts.campaign_id,
        release_sha=None if facts is None else facts.release_sha,
        binding_sha256=None if facts is None else facts.binding_sha256,
        observed_slots=tuple(sorted(observed_slots)),
        # The status deliberately never becomes an authority.  These fields
        # are constants rather than policy flags so an accidental caller
        # cannot convert a local report into a transport/promotion permit.
        external_execution_authorized=False,
        promotion_authorized=False,
        execution_authorized=False,
    )


def _expect_verified_deployment(
    value: object,
    *,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> None:
    require_verified_physical_full_matrix_deployment_preflight_posture(
        value,
        binding=binding,
        now=now,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
    )


def _expect_verified_strict_writer_response(
    value: object,
    *,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> None:
    """Accept only the owning boundary's opaque durable commit observation.

    The historical data-class verifier remains for narrow compatibility tests,
    but is deliberately not an oracle input: booleans such as
    ``durable_commit_coupled`` cannot prove an FI transaction waited for the
    exact signed IR receipt.  Import lazily so the root-owned adapter remains
    independent of this no-I/O reporting module.
    """

    try:
        from core.physical_strict_remote_ack_writer_response import (
            PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_OBSERVATION_SCHEMA,
            PhysicalStrictRemoteAckWriterResponseError,
            project_verified_physical_strict_remote_ack_writer_response_observation,
        )
    except ImportError as exc:
        raise PhysicalFullMatrixCampaignReadinessError(
            "STRICT_REMOTE_ACK_WRITER_RESPONSE_OWNING_BOUNDARY_REQUIRED"
        ) from exc
    try:
        projection = project_verified_physical_strict_remote_ack_writer_response_observation(
            value,
            now=now,
        )
    except PhysicalStrictRemoteAckWriterResponseError as exc:
        raise PhysicalFullMatrixCampaignReadinessError(
            "STRICT_REMOTE_ACK_WRITER_RESPONSE_OWNING_BOUNDARY_REQUIRED"
        ) from exc

    maximum = _maximum_evidence_age(
        maximum_evidence_age_seconds,
        code="STRICT_REMOTE_ACK_WRITER_RESPONSE_INVALID",
    )
    _fresh(
        projection.observed_at,
        now=now,
        maximum_age_seconds=maximum,
        code="STRICT_REMOTE_ACK_WRITER_RESPONSE_INVALID",
    )
    if (
        projection.schema != PHYSICAL_STRICT_REMOTE_ACK_WRITER_RESPONSE_OBSERVATION_SCHEMA
        or projection.source_site != binding.source_site
        or projection.destination_site != binding.destination_site
        or projection.campaign_id != binding.campaign_id
        or projection.release_sha != binding.release_sha
        or projection.schema_revision != binding.schema_revision
        or projection.baseline_generation_id != binding.baseline_generation_id
        or projection.baseline_manifest_sha256 != binding.baseline_manifest_sha256
        or projection.baseline_wal_lsn != binding.baseline_wal_lsn
        or type(projection.timeline_id) is not int
        or projection.timeline_id != binding.timeline_id
        or projection.stream_generation_id != binding.stream_generation_id
        or projection.destination_age_recipient != binding.destination_age_recipient
        or projection.route_binding_sha256 != binding.route_binding_sha256
        or type(projection.writer_epoch) is not int
        or projection.writer_epoch != binding.writer_epoch
        or projection.writer_lease_id != binding.writer_lease_id
        or projection.witness_transition_id != binding.witness_transition_id
        or projection.witnessed_term_proof_sha256 != binding.witnessed_term_proof_sha256
        or projection.target_acknowledged_wal_lsn != binding.target_acknowledged_wal_lsn
        or projection.blob_object_frontier_wal_lsn != binding.blob_object_frontier_wal_lsn
    ):
        _fail("STRICT_REMOTE_ACK_WRITER_RESPONSE_BINDING_MISMATCH")


def _expect_verified_arvan_immutability_preflight(
    value: object,
    *,
    preflight_binding: object,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> None:
    """Accept only opaque, fresh disposable-bucket immutability evidence.

    Provider-side versioning alone is not an immutable-retention guarantee.
    Keep this boundary lazy so the readiness oracle still performs no SDK or
    credential work; the owning preflight module verifies the root-owned,
    injected provider observation before exposing its non-authorizing
    projection here.
    """

    try:
        from core.physical_arvan_immutability_preflight import (
            PhysicalArvanImmutabilityPreflightError,
            PhysicalArvanImmutabilityPreflightBinding,
            project_verified_physical_arvan_immutability_preflight,
        )
    except ImportError as exc:
        raise PhysicalFullMatrixCampaignReadinessError(
            "ARVAN_IMMUTABILITY_PREFLIGHT_OWNING_BOUNDARY_REQUIRED"
        ) from exc
    if type(preflight_binding) is not PhysicalArvanImmutabilityPreflightBinding:
        _fail("ARVAN_IMMUTABILITY_PREFLIGHT_BINDING_INVALID")
    try:
        projection = project_verified_physical_arvan_immutability_preflight(
            value,
            binding=preflight_binding,
            now=now,
            maximum_evidence_age_seconds=maximum_evidence_age_seconds,
        )
    except PhysicalArvanImmutabilityPreflightError as exc:
        raise PhysicalFullMatrixCampaignReadinessError(
            "ARVAN_IMMUTABILITY_PREFLIGHT_INVALID"
        ) from exc
    if (
        projection.campaign_id != binding.campaign_id
        or projection.release_sha != binding.release_sha
        or projection.source_site != binding.source_site
        or projection.destination_site != binding.destination_site
        or projection.route_binding_sha256 != binding.route_binding_sha256
    ):
        _fail("ARVAN_IMMUTABILITY_PREFLIGHT_BINDING_MISMATCH")


def _expect_verified_arvan_four_role_immutability_preflight(
    value: object,
    *,
    preflight_config: object,
    live_iam_durable_admission: object,
    live_iam_binding: object,
    failback_binding: object,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> None:
    """Project only fresh opaque four-role immutable-storage evidence.

    This remains a lazy owning-boundary call.  The readiness oracle neither
    opens Object Storage nor trusts a raw IAM gate; the immutable-storage
    contract must revalidate its opaque durable admission against the exact
    live-IAM and failback route bindings before it exposes a non-authorizing
    projection here.
    """

    try:
        from core.physical_arvan_s3_four_role_immutability_preflight import (
            PhysicalArvanS3FourRoleImmutabilityPreflightBinding,
            PhysicalArvanS3FourRoleImmutabilityPreflightConfig,
            PhysicalArvanS3FourRoleImmutabilityPreflightError,
            PhysicalArvanS3FourRoleImmutabilityPreflightProjection,
            project_verified_physical_arvan_s3_four_role_immutability_preflight,
        )
        from core.physical_arvan_s3_four_role_live_iam_durable_admission_bridge import (
            VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission,
        )
        from core.physical_arvan_s3_four_role_live_iam_evidence import (
            PhysicalArvanS3FourRoleLiveIamEvidenceBinding,
        )
        from core.physical_ir_to_fi_object_storage_failback_preflight import (
            PhysicalIrToFiObjectStorageFailbackBinding,
        )
    except ImportError as exc:
        raise PhysicalFullMatrixCampaignReadinessError(
            "ARVAN_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_OWNING_BOUNDARY_REQUIRED"
        ) from exc

    if (
        type(preflight_config) is not PhysicalArvanS3FourRoleImmutabilityPreflightConfig
        or type(preflight_config.binding)
        is not PhysicalArvanS3FourRoleImmutabilityPreflightBinding
        or type(live_iam_durable_admission)
        is not VerifiedPhysicalArvanS3FourRoleLiveIamDurableAdmission
        or type(live_iam_binding) is not PhysicalArvanS3FourRoleLiveIamEvidenceBinding
        or type(failback_binding) is not PhysicalIrToFiObjectStorageFailbackBinding
    ):
        _fail("ARVAN_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_INPUT_INVALID")

    try:
        projection = project_verified_physical_arvan_s3_four_role_immutability_preflight(
            value,
            config=preflight_config,
            admission=live_iam_durable_admission,
            live_iam_binding=live_iam_binding,
            failback_binding=failback_binding,
            observed_at=now,
        )
    except (
        PhysicalArvanS3FourRoleImmutabilityPreflightError,
        TypeError,
        AttributeError,
    ) as exc:
        raise PhysicalFullMatrixCampaignReadinessError(
            "ARVAN_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_INVALID"
        ) from exc
    if type(projection) is not PhysicalArvanS3FourRoleImmutabilityPreflightProjection:
        _fail("ARVAN_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_INVALID")

    _fresh(
        projection.observed_at,
        now=now,
        maximum_age_seconds=maximum_evidence_age_seconds,
        code="ARVAN_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_STALE",
    )
    immutable_binding = preflight_config.binding
    if (
        projection.campaign_id != binding.campaign_id
        or projection.release_sha != binding.release_sha
        or projection.campaign_id != immutable_binding.campaign_id
        or projection.release_sha != immutable_binding.release_sha
        or projection.normal_route_scope_sha256
        != immutable_binding.normal_route_scope_sha256
        or projection.reverse_route_scope_sha256
        != immutable_binding.reverse_route_scope_sha256
        or projection.four_role_route_binding_sha256
        != immutable_binding.four_role_route_binding_sha256
        or projection.minimum_retention_days != immutable_binding.minimum_retention_days
        or immutable_binding.campaign_id != live_iam_binding.campaign_id
        or immutable_binding.release_sha != live_iam_binding.release_sha
        or immutable_binding.campaign_id != failback_binding.campaign_id
        or immutable_binding.release_sha != failback_binding.release_sha
        or projection.normal_route_scope_sha256
        != live_iam_binding.normal_route_scope_sha256
        or projection.reverse_route_scope_sha256
        != live_iam_binding.reverse_route_scope_sha256
        or projection.four_role_route_binding_sha256
        != live_iam_binding.four_role_binding_sha256
        or projection.normal_route_scope_sha256
        != failback_binding.normal_route_scope_sha256
        or projection.reverse_route_scope_sha256
        != failback_binding.reverse_route_scope_sha256
        or projection.four_role_route_binding_sha256
        != failback_binding.route_binding_sha256
        or immutable_binding.fi_publisher_identity_sha256
        != live_iam_binding.fi_publisher_identity_sha256
        or immutable_binding.ir_receiver_identity_sha256
        != live_iam_binding.ir_receiver_identity_sha256
        or immutable_binding.ir_publisher_identity_sha256
        != live_iam_binding.ir_publisher_identity_sha256
        or immutable_binding.fi_receiver_identity_sha256
        != live_iam_binding.fi_receiver_identity_sha256
        or immutable_binding.fi_publisher_identity_sha256
        != failback_binding.fi_publisher_identity_sha256
        or immutable_binding.ir_receiver_identity_sha256
        != failback_binding.ir_receiver_identity_sha256
        or immutable_binding.ir_publisher_identity_sha256
        != failback_binding.ir_publisher_identity_sha256
        or immutable_binding.fi_receiver_identity_sha256
        != failback_binding.fi_receiver_identity_sha256
        or projection.admission_aggregate_sha256
        != live_iam_durable_admission.aggregate_sha256
        or projection.admission_durable_ledger_head_sha256
        != live_iam_durable_admission.durable_ledger_head_sha256
    ):
        _fail("ARVAN_FOUR_ROLE_IMMUTABILITY_PREFLIGHT_BINDING_MISMATCH")


def _expect_verified_arvan_failback_preflight(
    value: object,
    *,
    preflight_config: object,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
) -> object:
    """Require a fresh four-identity reverse-route proof as well as normal proof.

    A Full Matrix necessarily contains the witnessed IR-writer interval.  The
    historical two-role immutability proof is therefore insufficient even
    while FI is currently the writer: the reverse ``IR -> Object Storage ->
    FI`` route must already have a separately pinned, fresh, four-identity
    admission.  This remains an observation-only check and performs no S3,
    credential, host, or Witness I/O.
    """

    try:
        from core.physical_ir_to_fi_object_storage_failback_preflight import (
            PhysicalIrToFiObjectStorageFailbackPreflightConfig,
            PhysicalIrToFiObjectStorageFailbackPreflightError,
            require_verified_physical_ir_to_fi_object_storage_failback_preflight,
        )
    except ImportError as exc:
        raise PhysicalFullMatrixCampaignReadinessError(
            "ARVAN_FAILBACK_PREFLIGHT_OWNING_BOUNDARY_REQUIRED"
        ) from exc
    if type(preflight_config) is not PhysicalIrToFiObjectStorageFailbackPreflightConfig:
        _fail("ARVAN_FAILBACK_PREFLIGHT_CONFIG_INVALID")
    try:
        verified = require_verified_physical_ir_to_fi_object_storage_failback_preflight(
            value,
            config=preflight_config,
            now=now,
        )
    except PhysicalIrToFiObjectStorageFailbackPreflightError as exc:
        raise PhysicalFullMatrixCampaignReadinessError(
            "ARVAN_FAILBACK_PREFLIGHT_INVALID"
        ) from exc
    reverse = verified.binding
    identities = (
        reverse.fi_publisher_identity_sha256,
        reverse.ir_receiver_identity_sha256,
        reverse.ir_publisher_identity_sha256,
        reverse.fi_receiver_identity_sha256,
    )
    if (
        reverse.campaign_id != binding.campaign_id
        or reverse.release_sha != binding.release_sha
        or reverse.source_site != "webapp_ir"
        or reverse.destination_site != "webapp_fi"
        or reverse.object_storage_namespace != "physical-failback"
        or len(set(identities)) != 4
    ):
        _fail("ARVAN_FAILBACK_PREFLIGHT_BINDING_MISMATCH")
    return reverse


def _expect_verified_external_effect_reconciliation(
    value: object,
    *,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> None:
    require_verified_physical_full_matrix_external_effect_reconciliation(
        value,
        binding=binding,
        now=now,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
    )


def _expect_verified_source_fence_route(
    value: object,
    *,
    binding: PhysicalFullMatrixCampaignBinding,
    now: datetime,
    maximum_evidence_age_seconds: int,
) -> None:
    require_verified_physical_full_matrix_source_fence_recovery_route(
        value,
        binding=binding,
        now=now,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
    )


def assess_physical_full_matrix_campaign_readiness(
    config: object,
    inputs: object,
    *,
    now: datetime,
) -> PhysicalFullMatrixCampaignReadiness:
    """Assess all physical Full-Matrix evidence slots without any I/O.

    The result is a fail-closed readiness *report*.  It has no path that calls
    replay, transport, routing, promotion, fencing, a database, Object
    Storage, Docker, SSH, a subprocess, or an old Full-Matrix runner.
    """

    reasons: set[str] = set()
    observed_slots: set[str] = set()
    facts: _BindingFacts | None = None

    # Clock and config are handled first because every opaque upstream type
    # performs its own time-bound revalidation.
    try:
        observed_now = _normalise_now(now)
    except PhysicalFullMatrixCampaignReadinessError:
        reasons.add("invalid-assessment-clock")
        return _readiness_report(facts=None, reasons=reasons, observed_slots=observed_slots)
    try:
        facts, maximum_age = _normalise_config(config)
    except PhysicalFullMatrixCampaignReadinessError as exc:
        reasons.add(
            "driver-disabled" if exc.code == "DRIVER_DISABLED" else "invalid-campaign-binding"
        )
        return _readiness_report(facts=None, reasons=reasons, observed_slots=observed_slots)
    if type(inputs) is not PhysicalFullMatrixCampaignInputs:
        reasons.add("invalid-campaign-inputs")
        return _readiness_report(facts=facts, reasons=reasons, observed_slots=observed_slots)

    # Old plans are rejected atomically before touching any evidence.  In
    # particular, no parser knows how to turn a two-server runner plan into a
    # physical campaign input.
    if _legacy_artifacts_present(inputs.legacy_runner_artifacts):
        reasons.add("legacy-runner-artifact-rejected")
        return _readiness_report(facts=facts, reasons=reasons, observed_slots=observed_slots)

    binding_value = config.binding

    # V2 recovery evidence is intentionally a separate migration slot.  It
    # proves a chunked recovery target and exact object coverage, but supplies
    # no V2 source request, signed receiver receipt, durable receiver ledger,
    # or strict writer-response coupling.  Do not feed it into any V1 helper
    # below and do not let it remove the explicit V1 activation fence.
    if inputs.v2_recovery_evidence is None:
        reasons.add(PHYSICAL_FULL_MATRIX_V2_CHUNKED_RECOVERY_EVIDENCE_MISSING_REASON)
    else:
        try:
            _v2_chunked_recovery_evidence_facts(
                inputs.v2_recovery_evidence,
                binding=facts,
                now=observed_now,
                maximum_evidence_age_seconds=maximum_age,
            )
            observed_slots.add(PHYSICAL_FULL_MATRIX_V2_CHUNKED_RECOVERY_EVIDENCE_SLOT)
            reasons.add(PHYSICAL_FULL_MATRIX_V2_STRICT_REMOTE_ACK_CHAIN_FENCE_REASON)
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add(PHYSICAL_FULL_MATRIX_V2_CHUNKED_RECOVERY_EVIDENCE_MISMATCH_REASON)

    bundle: VerifiedPhysicalWalObjectStorageBundle | None = None
    if inputs.physical_wal_bundle is None:
        reasons.add("missing-physical-wal-bundle")
    else:
        # Every bundle accepted by the current verifier has one immutable
        # ``base_backup_object``.  It remains useful as forensic/read-only
        # evidence, but cannot satisfy activation until the independently
        # reviewed v2 chunked publisher *and* receiver contract replaces this
        # slot.  Do not inspect a caller-provided type discriminator: doing so
        # would create an alternate V1 compatibility admission path.
        reasons.add(PHYSICAL_FULL_MATRIX_V1_SINGLE_OBJECT_BASE_BACKUP_ACTIVATION_FENCE_REASON)
        try:
            bundle = _bundle_facts(inputs.physical_wal_bundle, binding=facts)
            # Preserve the parsed bundle only to produce useful downstream
            # mismatch diagnostics.  It must not fill the required readiness
            # slot: the execution driver independently requires that slot,
            # providing a second fence even if a caller later mishandles a
            # reason code.
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("physical-wal-bundle-mismatch")

    if inputs.recovery_observation is None:
        reasons.add("missing-physical-wal-recovery-observation")
    elif bundle is not None:
        try:
            _recovery_observation_facts(
                inputs.recovery_observation,
                bundle=bundle,
                binding=facts,
                now=observed_now,
                maximum_evidence_age_seconds=maximum_age,
            )
            observed_slots.add("physical-wal-recovery-observation")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("physical-wal-recovery-observation-mismatch")
    else:
        reasons.add("physical-wal-recovery-observation-mismatch")

    remote: VerifiedPhysicalWalRemoteAckEvidence | None = None
    request: object | None = None
    if inputs.remote_ack_evidence is None:
        reasons.add("missing-remote-ack-evidence")
    elif bundle is not None:
        try:
            remote, request = _remote_ack_facts(
                inputs.remote_ack_evidence,
                bundle=bundle,
                binding=facts,
                now=observed_now,
            )
            observed_slots.add("remote-ack-evidence")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("remote-ack-evidence-mismatch")
    else:
        reasons.add("remote-ack-evidence-mismatch")

    recovery: VerifiedPhysicalWalRemoteAckReceiverRecoveryEvidence | None = None
    if inputs.remote_ack_receiver_recovery is None:
        reasons.add("missing-remote-ack-receiver-recovery")
    elif request is not None:
        try:
            recovery = _remote_ack_receiver_recovery_facts(
                inputs.remote_ack_receiver_recovery,
                request=request,
                binding=facts,
                now=observed_now,
            )
            observed_slots.add("remote-ack-receiver-recovery")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("remote-ack-receiver-recovery-mismatch")
    else:
        reasons.add("remote-ack-receiver-recovery-mismatch")

    if inputs.remote_ack_durable_ledger is None:
        reasons.add("missing-remote-ack-durable-ledger")
    elif remote is not None and recovery is not None:
        try:
            _remote_ack_durable_ledger_facts(
                inputs.remote_ack_durable_ledger,
                remote=remote,
                recovery=recovery,
                binding=facts,
            )
            observed_slots.add("remote-ack-durable-ledger")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("remote-ack-durable-ledger-mismatch")
    else:
        reasons.add("remote-ack-durable-ledger-mismatch")

    if inputs.strict_remote_ack_writer_response is None:
        reasons.add("missing-strict-remote-ack-writer-response")
    else:
        try:
            _expect_verified_strict_writer_response(
                inputs.strict_remote_ack_writer_response,
                binding=binding_value,
                now=observed_now,
                maximum_evidence_age_seconds=maximum_age,
            )
            observed_slots.add("strict-remote-ack-writer-response")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("strict-remote-ack-writer-response-mismatch")

    # Do not let the retired normal-only, two-identity proof contribute to a
    # reversible campaign.  Keeping the injected fields avoids silently
    # accepting an old caller shape, while making every such attempt visible
    # in the fail-closed report.  The four-role replacement gets its own
    # explicit slots below.
    if (
        inputs.arvan_immutability_preflight is not None
        or inputs.arvan_immutability_preflight_binding is not None
    ):
        reasons.add("legacy-arvan-object-storage-immutability-preflight-rejected")

    four_role_immutability_failback_binding: object | None = None
    if inputs.arvan_four_role_immutability_preflight is None:
        reasons.add("missing-four-role-arvan-object-storage-immutability-preflight")
    elif (
        inputs.arvan_four_role_immutability_preflight_config is None
        or inputs.arvan_four_role_immutability_live_iam_durable_admission is None
        or inputs.arvan_four_role_immutability_live_iam_binding is None
        or inputs.arvan_four_role_immutability_failback_binding is None
    ):
        reasons.add("four-role-arvan-object-storage-immutability-preflight-mismatch")
    else:
        try:
            _expect_verified_arvan_four_role_immutability_preflight(
                inputs.arvan_four_role_immutability_preflight,
                preflight_config=inputs.arvan_four_role_immutability_preflight_config,
                live_iam_durable_admission=(
                    inputs.arvan_four_role_immutability_live_iam_durable_admission
                ),
                live_iam_binding=inputs.arvan_four_role_immutability_live_iam_binding,
                failback_binding=inputs.arvan_four_role_immutability_failback_binding,
                binding=binding_value,
                now=observed_now,
                maximum_evidence_age_seconds=maximum_age,
            )
            observed_slots.add("four-role-arvan-object-storage-immutability-preflight")
            four_role_immutability_failback_binding = (
                inputs.arvan_four_role_immutability_failback_binding
            )
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("four-role-arvan-object-storage-immutability-preflight-mismatch")

    verified_failback_binding: object | None = None
    if inputs.arvan_failback_preflight is None:
        reasons.add("missing-arvan-object-storage-failback-preflight")
    elif inputs.arvan_failback_preflight_config is None:
        reasons.add("arvan-object-storage-failback-preflight-mismatch")
    else:
        try:
            verified_failback_binding = _expect_verified_arvan_failback_preflight(
                inputs.arvan_failback_preflight,
                preflight_config=inputs.arvan_failback_preflight_config,
                binding=binding_value,
                now=observed_now,
            )
            observed_slots.add("arvan-object-storage-failback-preflight")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("arvan-object-storage-failback-preflight-mismatch")

    if (
        four_role_immutability_failback_binding is not None
        and verified_failback_binding is not None
        and four_role_immutability_failback_binding != verified_failback_binding
    ):
        # The independent reverse preflight is useful only when it pins the
        # exact same four-role route already used to revalidate Object Lock.
        # Do not let two individually valid but different routes compose into
        # one readiness report.
        observed_slots.discard("four-role-arvan-object-storage-immutability-preflight")
        reasons.add("four-role-arvan-object-storage-immutability-preflight-mismatch")

    if inputs.blob_promotion_evidence is None:
        reasons.add("missing-blob-promotion-evidence")
    elif inputs.blob_storage_binding is None or inputs.blob_promotion_config is None:
        reasons.add("blob-promotion-evidence-mismatch")
    else:
        try:
            _blob_promotion_facts(
                evidence_value=inputs.blob_promotion_evidence,
                storage_binding_value=inputs.blob_storage_binding,
                config_value=inputs.blob_promotion_config,
                binding=facts,
                now=observed_now,
            )
            observed_slots.add("receiver-ready-v2-blob-promotion-evidence")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("blob-promotion-evidence-mismatch")

    if inputs.witnessed_term is None:
        reasons.add("missing-current-witness-term")
    else:
        try:
            _witness_term_facts(inputs.witnessed_term, binding=facts, now=observed_now)
            observed_slots.add("current-witness-term")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("witness-term-mismatch")

    if inputs.role_activation is None:
        reasons.add("missing-current-role-activation")
    else:
        try:
            _role_activation_facts(inputs.role_activation, binding=facts, now=observed_now)
            observed_slots.add("current-role-activation")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("role-activation-mismatch")

    if inputs.deployment_preflight_posture is None:
        reasons.add("missing-deployment-preflight-posture")
    else:
        try:
            _expect_verified_deployment(
                inputs.deployment_preflight_posture,
                binding=binding_value,
                now=observed_now,
                maximum_evidence_age_seconds=maximum_age,
            )
            observed_slots.add("deployment-preflight-posture")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("deployment-preflight-posture-mismatch")

    if inputs.p0_auth_upload_result is None:
        reasons.add("missing-p0-auth-upload-result")
    else:
        try:
            _p0_auth_upload_facts(
                inputs.p0_auth_upload_result,
                binding=facts,
                now=observed_now,
                maximum_evidence_age_seconds=maximum_age,
            )
            observed_slots.add("selected-p0-auth-upload-result")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("p0-auth-upload-result-mismatch")

    if inputs.external_effect_reconciliation is None:
        reasons.add("missing-external-effect-reconciliation-decision")
    else:
        try:
            _expect_verified_external_effect_reconciliation(
                inputs.external_effect_reconciliation,
                binding=binding_value,
                now=observed_now,
                maximum_evidence_age_seconds=maximum_age,
            )
            observed_slots.add("external-effect-reconciliation-decision")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("external-effect-reconciliation-mismatch")

    if inputs.source_fence_recovery_route is None:
        reasons.add("missing-source-write-fence-recovery-route")
    else:
        try:
            _expect_verified_source_fence_route(
                inputs.source_fence_recovery_route,
                binding=binding_value,
                now=observed_now,
                maximum_evidence_age_seconds=maximum_age,
            )
            observed_slots.add("source-write-fence-recovery-route")
        except PhysicalFullMatrixCampaignReadinessError:
            reasons.add("source-write-fence-recovery-route-mismatch")

    return _readiness_report(facts=facts, reasons=reasons, observed_slots=observed_slots)


def _require_positive_readiness_report(
    value: object,
    *,
    code: str,
) -> PhysicalFullMatrixCampaignReadiness:
    """Validate the non-authorizing shape that may seed verified provenance."""

    if (
        type(value) is not PhysicalFullMatrixCampaignReadiness
        or value.schema != PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_SCHEMA
        or value.status != PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_STATUS_LOCAL_EVIDENCE_OBSERVED
        or value.reason_codes != ()
        or type(value.campaign_id) is not str
        or not value.campaign_id
        or type(value.release_sha) is not str
        or not value.release_sha
        or type(value.binding_sha256) is not str
        or not value.binding_sha256
        or type(value.observed_slots) is not tuple
        or value.external_execution_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail(code)
    return value


def mint_verified_physical_full_matrix_campaign_readiness(
    *,
    config: object,
    inputs: object,
    now: datetime,
) -> VerifiedPhysicalFullMatrixCampaignReadiness:
    """Mint process-local provenance only after a genuinely positive assessment.

    The public report remains useful for diagnostics, but it is deliberately
    insufficient for an execution boundary.  Retaining the exact local
    configuration and injected capabilities in private weak state lets that
    boundary re-assess them at its own clock without serializing secrets or
    treating a report-shaped data class as an authorization.
    """

    report = _require_positive_readiness_report(
        assess_physical_full_matrix_campaign_readiness(config, inputs, now=now),
        code="PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_POSITIVE_REQUIRED",
    )
    result = VerifiedPhysicalFullMatrixCampaignReadiness(report=report)
    object.__setattr__(result, "_capability", _VERIFIED_READINESS_CAPABILITY)
    _VERIFIED_READINESS_STATES[result] = _VerifiedPhysicalFullMatrixCampaignReadinessState(
        config=config,
        inputs=inputs,
        report=report,
    )
    return result


def require_verified_physical_full_matrix_campaign_readiness(
    value: object,
    *,
    now: datetime | None = None,
) -> PhysicalFullMatrixCampaignReadiness:
    """Return a process-local positive report, re-assessing it when clocked.

    ``now=None`` is intentionally limited to a membership/provenance check for
    non-authorizing plan construction.  Any effectful caller must provide its
    current clock, which re-runs the no-I/O assessment over the exact retained
    config and inputs before exposing the report.
    """

    if (
        type(value) is not VerifiedPhysicalFullMatrixCampaignReadiness
        or value._capability is not _VERIFIED_READINESS_CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_CAPABILITY_REQUIRED")
    state = _VERIFIED_READINESS_STATES.get(value)
    if state is None or value.report is not state.report:
        _fail("PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_CAPABILITY_REQUIRED")
    report = _require_positive_readiness_report(
        state.report,
        code="PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_CAPABILITY_TAMPERED",
    )
    if now is None:
        return report
    try:
        rechecked = assess_physical_full_matrix_campaign_readiness(
            state.config,
            state.inputs,
            now=now,
        )
    except Exception as exc:
        raise PhysicalFullMatrixCampaignReadinessError(
            "PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_REVALIDATION_FAILED"
        ) from exc
    rechecked = _require_positive_readiness_report(
        rechecked,
        code="PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_REVALIDATION_BLOCKED",
    )
    if rechecked != report:
        _fail("PHYSICAL_FULL_MATRIX_CAMPAIGN_READINESS_REVALIDATION_MISMATCH")
    return report
