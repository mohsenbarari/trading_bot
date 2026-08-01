"""Fail-closed V2-only join for the physical Full-Matrix ACK chain.

This module deliberately replaces neither the V1 campaign-readiness boundary
nor its driver.  It accepts only process-local V2 capabilities, revalidates
each owning boundary at one supplied clock, and binds the resulting facts into
one opaque observation.  It performs no I/O and never grants a write,
promotion, deployment, or execution permit.

The narrow boundary matters because a signed V2 pair, a local recovery
observation, and a durable receiver ledger entry are independently useful but
are not interchangeable.  In particular the strict writer response must bind
the *same* request, receipt, durable entry and witnessed writer term as the
chunked recovery proof.  A future V2 readiness generation can consume only
this join instead of composing those values ad hoc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any
from weakref import WeakKeyDictionary

from core.append_only_sync_delta_batch import (
    CAMPAIGN_ID_RE,
    LEASE_ID_RE,
    RELEASE_SHA_RE,
    SHA256_RE,
    WEBAPP_SITES,
    canonical_json_bytes,
)
from core.physical_full_matrix_v2_recovery_evidence import (
    PhysicalFullMatrixV2RecoveryEvidenceError,
    VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    require_verified_physical_full_matrix_v2_recovery_evidence,
)
from core.physical_wal_v2_remote_ack import (
    PhysicalWalV2RemoteAckConfig,
    PhysicalWalV2RemoteAckError,
    VerifiedPhysicalWalV2RemoteAckEvidence,
    VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    VerifiedPhysicalWalV2RemoteAckRequest,
    require_verified_physical_wal_v2_remote_ack_evidence,
    require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence,
    require_verified_physical_wal_v2_remote_ack_request,
)
from core.physical_wal_v2_remote_ack_receiver_ledger import (
    PhysicalWalV2RemoteAckReceiverLedgerConfig,
    PhysicalWalV2RemoteAckReceiverLedgerError,
    VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt,
    require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt,
)
from core.physical_wal_v2_strict_remote_ack_writer_response import (
    PhysicalWalV2StrictRemoteAckWriterResponseConfig,
    PhysicalWalV2StrictRemoteAckWriterResponseError,
    VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation,
    require_verified_physical_wal_v2_strict_remote_ack_writer_response_observation,
)


__all__ = (
    "PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_SCHEMA",
    "PhysicalFullMatrixV2AckChainConfig",
    "PhysicalFullMatrixV2AckChainError",
    "PhysicalFullMatrixV2AckChainInputs",
    "VerifiedPhysicalFullMatrixV2AckChain",
    "mint_verified_physical_full_matrix_v2_ack_chain",
    "require_verified_physical_full_matrix_v2_ack_chain",
)


PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_SCHEMA = (
    "gold-trade-physical-full-matrix-v2-ack-chain-v1"
)
PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_DEFAULT_ENABLED = False

_CAPABILITY = object()
_ZERO_SHA256 = "0" * 64


class PhysicalFullMatrixV2AckChainError(ValueError):
    """A V2-only ACK-chain input is foreign, stale, or cross-bound wrongly."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PhysicalFullMatrixV2AckChainConfig:
    """Exact V2 verifier policies for one normal or failback direction.

    The three policies remain explicit because they are owned by separate
    boundaries.  They must nevertheless describe the identical V2 context.
    This structure contains no endpoint, credential, host path, transport, or
    effect permission.
    """

    remote_ack_config: PhysicalWalV2RemoteAckConfig | None = None
    receiver_ledger_config: PhysicalWalV2RemoteAckReceiverLedgerConfig | None = None
    strict_writer_config: PhysicalWalV2StrictRemoteAckWriterResponseConfig | None = None
    enabled: bool = PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_DEFAULT_ENABLED


@dataclass(frozen=True)
class PhysicalFullMatrixV2AckChainInputs:
    """The complete V2-only evidence chain for one exact durable ACK.

    ``receiver_ledger_receipt`` remains in this shape solely so the boundary
    can explicitly reject a tempting but unsafe local composition in tests.
    It is an IR-local capability and is never a transferable FI readiness
    input.  A future ``witness_mediated_v2_roundtrip`` owner must prove the
    complete ``FI outbox -> Witness -> IR -> Witness -> FI`` request/receipt
    path before this boundary can mint a result.
    """

    recovery_evidence: VerifiedPhysicalFullMatrixV2RecoveryEvidence | None = None
    source_request: VerifiedPhysicalWalV2RemoteAckRequest | None = None
    receiver_recovery_evidence: (
        VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence | None
    ) = None
    remote_ack_evidence: VerifiedPhysicalWalV2RemoteAckEvidence | None = None
    receiver_ledger_receipt: (
        VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt | None
    ) = None
    strict_writer_response: (
        VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation | None
    ) = None
    witness_mediated_v2_roundtrip: object | None = None


@dataclass(frozen=True, eq=False)
class VerifiedPhysicalFullMatrixV2AckChain:
    """Opaque, revalidatable V2 ACK-chain observation; never an authority."""

    schema: str
    chain_sha256: str
    campaign_id: str
    release_sha: str
    source_site: str
    destination_site: str
    route_commitment_sha256: str
    four_role_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    context_sha256: str
    source_request_sha256: str
    request_id: str
    request_nonce: str
    destination_receipt_sha256: str
    receipt_id: str
    receipt_nonce: str
    durable_ledger_entry_sha256: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    receiver_recovery_evidence_sha256: str
    target_lsn: str
    receiver_replay_lsn: str
    object_version_set_sha256: str
    strict_commit_record_id: str
    strict_response_id: str
    strict_response_committed_at: datetime
    strict_response_sha256: str
    recovery_authorized: bool = False
    promotion_authorized: bool = False
    execution_authorized: bool = False
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_SERIALIZATION_FORBIDDEN")

    def __copy__(self) -> object:
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_COPY_FORBIDDEN")

    def __deepcopy__(self, memo: object) -> object:
        del memo
        raise TypeError("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_COPY_FORBIDDEN")


@dataclass(frozen=True)
class _State:
    config: PhysicalFullMatrixV2AckChainConfig
    inputs: PhysicalFullMatrixV2AckChainInputs


_STATES: WeakKeyDictionary[VerifiedPhysicalFullMatrixV2AckChain, _State] = (
    WeakKeyDictionary()
)


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV2AckChainError(code)


def _utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None or value == _ZERO_SHA256:
        _fail(code)
    return value


def _site(value: object, *, code: str) -> str:
    if type(value) is not str or value not in WEBAPP_SITES:
        _fail(code)
    return value


def _identifier(value: object, *, code: str) -> str:
    if type(value) is not str or not value or len(value) > 255:
        _fail(code)
    try:
        value.encode("ascii", "strict")
    except UnicodeEncodeError:
        _fail(code)
    if any(character.isspace() for character in value):
        _fail(code)
    return value


def _config(
    value: object,
) -> tuple[
    PhysicalWalV2RemoteAckConfig,
    PhysicalWalV2RemoteAckReceiverLedgerConfig,
    PhysicalWalV2StrictRemoteAckWriterResponseConfig,
]:
    if (
        type(value) is not PhysicalFullMatrixV2AckChainConfig
        or value.enabled is not True
        or type(value.remote_ack_config) is not PhysicalWalV2RemoteAckConfig
        or type(value.receiver_ledger_config) is not PhysicalWalV2RemoteAckReceiverLedgerConfig
        or type(value.strict_writer_config)
        is not PhysicalWalV2StrictRemoteAckWriterResponseConfig
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_CONFIG_INVALID")
    remote = value.remote_ack_config
    ledger = value.receiver_ledger_config
    strict = value.strict_writer_config
    if (
        remote.enabled is not True
        or ledger.enabled is not True
        or strict.enabled is not True
        or ledger.remote_ack_config != remote
        or strict.remote_ack_config != remote
        or strict.receiver_ledger_config != ledger
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_CONFIG_MISMATCH")
    return remote, ledger, strict


def _inputs(value: object) -> PhysicalFullMatrixV2AckChainInputs:
    if type(value) is not PhysicalFullMatrixV2AckChainInputs:
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_INPUTS_INVALID")
    required = (
        (value.recovery_evidence, VerifiedPhysicalFullMatrixV2RecoveryEvidence),
        (value.source_request, VerifiedPhysicalWalV2RemoteAckRequest),
        (
            value.receiver_recovery_evidence,
            VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
        ),
        (value.remote_ack_evidence, VerifiedPhysicalWalV2RemoteAckEvidence),
        (
            value.receiver_ledger_receipt,
            VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt,
        ),
        (
            value.strict_writer_response,
            VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation,
        ),
    )
    if any(type(item) is not expected for item, expected in required):
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_INPUTS_INVALID")
    return value


def _strict_term_facts(
    strict: VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation,
    *,
    recovery: VerifiedPhysicalFullMatrixV2RecoveryEvidence,
) -> tuple[str, int, str, str]:
    """Require the strict boundary to expose its term, not hide it in a bool.

    The strict writer runtime owns live Witness/activation validation.  The
    aggregate still has to compare its redacted term pins with the recovery
    binding; otherwise a valid strict response for a successor term could be
    combined with an old V2 recovery chain.  Older placeholder strict objects
    lack these fields and therefore remain fail-closed by design.
    """

    holder = _site(
        getattr(strict, "writer_holder_site", None),
        code="PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_STRICT_TERM_REQUIRED",
    )
    epoch = getattr(strict, "writer_epoch", None)
    if type(epoch) is not int or not 1 <= epoch <= 2**31 - 1:
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_STRICT_TERM_REQUIRED")
    lease = getattr(strict, "writer_lease_id", None)
    if type(lease) is not str or LEASE_ID_RE.fullmatch(lease) is None:
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_STRICT_TERM_REQUIRED")
    term = _sha256(
        getattr(strict, "witnessed_term_proof_sha256", None),
        code="PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_STRICT_TERM_REQUIRED",
    )
    binding = recovery.transfer_binding.writer_term
    if (
        holder != binding.writer_holder_site
        or epoch != binding.writer_epoch
        or lease != binding.writer_lease_id
        or term != binding.witnessed_term_proof_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_STRICT_TERM_MISMATCH")
    return holder, epoch, lease, term


def _result(
    *,
    recovery: VerifiedPhysicalFullMatrixV2RecoveryEvidence,
    request: VerifiedPhysicalWalV2RemoteAckRequest,
    receiver_recovery: VerifiedPhysicalWalV2RemoteAckReceiverRecoveryEvidence,
    remote_ack: VerifiedPhysicalWalV2RemoteAckEvidence,
    ledger: VerifiedPhysicalWalV2RemoteAckReceiverLedgerReceipt,
    strict: VerifiedPhysicalWalV2StrictRemoteAckWriterResponseObservation,
) -> VerifiedPhysicalFullMatrixV2AckChain:
    """Cross-pin every public V2 commitment before constructing the join."""

    binding = recovery.transfer_binding
    source_request_sha = hashlib.sha256(request.canonical_request).hexdigest()
    destination_receipt_sha = hashlib.sha256(remote_ack.canonical_receipt).hexdigest()
    recovery_record = receiver_recovery.evidence
    holder, epoch, lease, term = _strict_term_facts(strict, recovery=recovery)

    if (
        request.source_site != binding.source_site
        or request.destination_site != binding.destination_site
        or request.context_sha256 != remote_ack.context_sha256
        or request.context_sha256 != receiver_recovery.context_sha256
        or request.context_sha256 != ledger.context_sha256
        or request.target_lsn != recovery.target_replay_lsn
        or request.object_version_set_sha256 != recovery.object_version_set_sha256
        or remote_ack.request_id != request.request_id
        or remote_ack.request_nonce != request.request_nonce
        or remote_ack.receiver_recovery_evidence_sha256
        != recovery_record.receiver_recovery_evidence_sha256
        or remote_ack.receiver_replay_lsn != recovery_record.replay_lsn
        or receiver_recovery.request_sha256 != source_request_sha
        or recovery_record.source_request_sha256 != source_request_sha
        or recovery_record.context_sha256 != request.context_sha256
        or recovery_record.source_site != binding.source_site
        or recovery_record.destination_site != binding.destination_site
        or recovery_record.receiver_site != binding.destination_site
        or recovery_record.object_version_set_sha256 != recovery.object_version_set_sha256
        or recovery_record.target_lsn != recovery.target_replay_lsn
        or ledger.canonical_source_request != request.canonical_request
        or ledger.canonical_destination_receipt != remote_ack.canonical_receipt
        or ledger.source_request_sha256 != source_request_sha
        or ledger.destination_receipt_sha256 != destination_receipt_sha
        or ledger.request_id != request.request_id
        or ledger.request_nonce != request.request_nonce
        or ledger.receipt_id != remote_ack.receipt_id
        or ledger.receipt_nonce != remote_ack.receipt_nonce
        or ledger.receiver_recovery_evidence_sha256
        != recovery_record.receiver_recovery_evidence_sha256
        or ledger.receiver_replay_lsn != recovery_record.replay_lsn
        or ledger.target_recovery_evidence_sha256 != recovery.evidence_sha256
        or ledger.readback_attestation_sha256 != recovery.readback_attestation_sha256
        or ledger.readback_attestation_id != recovery.readback_attestation_id
        or ledger.readback_attestation_nonce != recovery.readback_attestation_nonce
        or ledger.stage_receipt_sha256 != recovery.stage_receipt_sha256
        or ledger.witness_transition_id != recovery.witness_transition_id
        or ledger.target_recovery_observed_at != recovery.observed_at
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_CROSS_PIN_MISMATCH")

    committed_at = _utc(
        getattr(strict, "committed_at", None),
        code="PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_STRICT_RESPONSE_INVALID",
    )
    local_commit = _identifier(
        getattr(strict, "local_commit_record_id", None),
        code="PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_STRICT_RESPONSE_INVALID",
    )
    local_response = _identifier(
        getattr(strict, "local_response_id", None),
        code="PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_STRICT_RESPONSE_INVALID",
    )
    if (
        getattr(strict, "schema", None)
        != "gold-trade-physical-wal-v2-strict-remote-ack-writer-response-v2"
        or getattr(strict, "context_sha256", None) != request.context_sha256
        or getattr(strict, "source_request_sha256", None) != source_request_sha
        or getattr(strict, "destination_receipt_sha256", None) != destination_receipt_sha
        or getattr(strict, "durable_ledger_entry_sha256", None)
        != ledger.durable_ledger_entry_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_STRICT_RESPONSE_MISMATCH")

    payload: dict[str, Any] = {
        "schema": PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_SCHEMA,
        "campaign_id": binding.campaign_id,
        "release_sha": binding.release_sha,
        "source_site": binding.source_site,
        "destination_site": binding.destination_site,
        "route_commitment_sha256": recovery.route_commitment_sha256,
        "four_role_binding_sha256": recovery.four_role_binding_sha256,
        "writer_holder_site": holder,
        "writer_epoch": epoch,
        "writer_lease_id": lease,
        "witnessed_term_proof_sha256": term,
        "context_sha256": request.context_sha256,
        "source_request_sha256": source_request_sha,
        "request_id": request.request_id,
        "request_nonce": request.request_nonce,
        "destination_receipt_sha256": destination_receipt_sha,
        "receipt_id": remote_ack.receipt_id,
        "receipt_nonce": remote_ack.receipt_nonce,
        "durable_ledger_entry_sha256": ledger.durable_ledger_entry_sha256,
        "target_recovery_evidence_sha256": recovery.evidence_sha256,
        "readback_attestation_sha256": recovery.readback_attestation_sha256,
        "receiver_recovery_evidence_sha256": recovery_record.receiver_recovery_evidence_sha256,
        "target_lsn": recovery.target_replay_lsn,
        "receiver_replay_lsn": recovery_record.replay_lsn,
        "object_version_set_sha256": recovery.object_version_set_sha256,
        "strict_commit_record_id": local_commit,
        "strict_response_id": local_response,
        "strict_response_committed_at": committed_at.isoformat(),
        "recovery_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }
    chain_sha = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    strict_sha = hashlib.sha256(
        canonical_json_bytes(
            {
                "schema": strict.schema,
                "context_sha256": strict.context_sha256,
                "source_request_sha256": strict.source_request_sha256,
                "destination_receipt_sha256": strict.destination_receipt_sha256,
                "durable_ledger_entry_sha256": strict.durable_ledger_entry_sha256,
                "writer_holder_site": holder,
                "writer_epoch": epoch,
                "writer_lease_id": lease,
                "witnessed_term_proof_sha256": term,
                "local_commit_record_id": local_commit,
                "local_response_id": local_response,
                "committed_at": committed_at.isoformat(),
            }
        )
    ).hexdigest()
    return VerifiedPhysicalFullMatrixV2AckChain(
        schema=PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_SCHEMA,
        chain_sha256=chain_sha,
        campaign_id=binding.campaign_id,
        release_sha=binding.release_sha,
        source_site=binding.source_site,
        destination_site=binding.destination_site,
        route_commitment_sha256=recovery.route_commitment_sha256,
        four_role_binding_sha256=recovery.four_role_binding_sha256,
        writer_holder_site=holder,
        writer_epoch=epoch,
        writer_lease_id=lease,
        witnessed_term_proof_sha256=term,
        context_sha256=request.context_sha256,
        source_request_sha256=source_request_sha,
        request_id=request.request_id,
        request_nonce=request.request_nonce,
        destination_receipt_sha256=destination_receipt_sha,
        receipt_id=remote_ack.receipt_id,
        receipt_nonce=remote_ack.receipt_nonce,
        durable_ledger_entry_sha256=ledger.durable_ledger_entry_sha256,
        target_recovery_evidence_sha256=recovery.evidence_sha256,
        readback_attestation_sha256=recovery.readback_attestation_sha256,
        receiver_recovery_evidence_sha256=recovery_record.receiver_recovery_evidence_sha256,
        target_lsn=recovery.target_replay_lsn,
        receiver_replay_lsn=recovery_record.replay_lsn,
        object_version_set_sha256=recovery.object_version_set_sha256,
        strict_commit_record_id=local_commit,
        strict_response_id=local_response,
        strict_response_committed_at=committed_at,
        strict_response_sha256=strict_sha,
    )


def _derive(
    *,
    config: object,
    inputs: object,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2AckChain:
    remote_config, ledger_config, strict_config = _config(config)
    supplied = _inputs(inputs)
    observed_now = _utc(now, code="PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_CLOCK_INVALID")
    # The durable receiver receipt and target-recovery capability are
    # intentionally process-local IR capabilities.  Accepting either here
    # would silently create FI <- IR control by serializing or sharing local
    # authority.  The V2 *source request* must not bypass Witness either: the
    # only admissible future path is FI outbox -> Witness -> IR followed by a
    # fresh Witness-mediated return to FI.  Do not even start deriving a
    # positive chain until a separately reviewed owner exposes that complete
    # opaque roundtrip projection.
    if supplied.witness_mediated_v2_roundtrip is None:
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_WITNESS_MEDIATED_ROUNDTRIP_REQUIRED")
    _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_WITNESS_MEDIATED_ROUNDTRIP_OWNING_BOUNDARY_REQUIRED")

    # Unreachable until the explicit Witness bridge is integrated.  Preserve
    # the exact local V2 cross-pin implementation below as a review target;
    # it must be replaced by the bridge's non-transferable projection rather
    # than made reachable with a raw receiver ledger capability.
    try:
        recovery = require_verified_physical_full_matrix_v2_recovery_evidence(
            supplied.recovery_evidence,
            now=observed_now,
        )
        request = require_verified_physical_wal_v2_remote_ack_request(
            supplied.source_request,
            config=remote_config,
            now=observed_now,
        )
        receiver_recovery = (
            require_verified_physical_wal_v2_remote_ack_receiver_recovery_evidence(
                supplied.receiver_recovery_evidence,
                source_request=request,
                config=remote_config,
                now=observed_now,
            )
        )
        remote_ack = require_verified_physical_wal_v2_remote_ack_evidence(
            supplied.remote_ack_evidence,
            config=remote_config,
            now=observed_now,
        )
        ledger = require_verified_physical_wal_v2_remote_ack_receiver_ledger_receipt(
            supplied.receiver_ledger_receipt,
            config=ledger_config,
            source_request=request,
            receiver_recovery_evidence=receiver_recovery,
            target_recovery_evidence=recovery,
            remote_ack_evidence=remote_ack,
            now=observed_now,
        )
        strict = require_verified_physical_wal_v2_strict_remote_ack_writer_response_observation(
            supplied.strict_writer_response,
            config=strict_config,
            now=observed_now,
        )
    except (
        PhysicalFullMatrixV2RecoveryEvidenceError,
        PhysicalWalV2RemoteAckError,
        PhysicalWalV2RemoteAckReceiverLedgerError,
        PhysicalWalV2StrictRemoteAckWriterResponseError,
        TypeError,
        ValueError,
    ) as exc:
        raise PhysicalFullMatrixV2AckChainError(
            "PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_UPSTREAM_INVALID"
        ) from exc
    return _result(
        recovery=recovery,
        request=request,
        receiver_recovery=receiver_recovery,
        remote_ack=remote_ack,
        ledger=ledger,
        strict=strict,
    )


def _assert_result(
    value: object,
    *,
    expected: VerifiedPhysicalFullMatrixV2AckChain,
) -> VerifiedPhysicalFullMatrixV2AckChain:
    if type(value) is not VerifiedPhysicalFullMatrixV2AckChain:
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_CAPABILITY_REQUIRED")
    for name in (
        "schema",
        "chain_sha256",
        "campaign_id",
        "release_sha",
        "source_site",
        "destination_site",
        "route_commitment_sha256",
        "four_role_binding_sha256",
        "writer_holder_site",
        "writer_epoch",
        "writer_lease_id",
        "witnessed_term_proof_sha256",
        "context_sha256",
        "source_request_sha256",
        "request_id",
        "request_nonce",
        "destination_receipt_sha256",
        "receipt_id",
        "receipt_nonce",
        "durable_ledger_entry_sha256",
        "target_recovery_evidence_sha256",
        "readback_attestation_sha256",
        "receiver_recovery_evidence_sha256",
        "target_lsn",
        "receiver_replay_lsn",
        "object_version_set_sha256",
        "strict_commit_record_id",
        "strict_response_id",
        "strict_response_committed_at",
        "strict_response_sha256",
        "recovery_authorized",
        "promotion_authorized",
        "execution_authorized",
    ):
        if getattr(value, name) != getattr(expected, name):
            _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_CAPABILITY_TAMPERED")
    if (
        value.schema != PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_SCHEMA
        or value.recovery_authorized is not False
        or value.promotion_authorized is not False
        or value.execution_authorized is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_CAPABILITY_TAMPERED")
    return value


def mint_verified_physical_full_matrix_v2_ack_chain(
    *,
    config: PhysicalFullMatrixV2AckChainConfig,
    inputs: PhysicalFullMatrixV2AckChainInputs,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2AckChain:
    """Mint a local V2 join only after all owning boundaries revalidate."""

    expected = _derive(config=config, inputs=inputs, now=now)
    object.__setattr__(expected, "_capability", _CAPABILITY)
    _STATES[expected] = _State(config=config, inputs=inputs)
    return _assert_result(expected, expected=expected)


def require_verified_physical_full_matrix_v2_ack_chain(
    value: object,
    *,
    config: PhysicalFullMatrixV2AckChainConfig,
    now: datetime,
) -> VerifiedPhysicalFullMatrixV2AckChain:
    """Revalidate every V2 input and every cross-pin at the current clock."""

    if (
        type(value) is not VerifiedPhysicalFullMatrixV2AckChain
        or value._capability is not _CAPABILITY
    ):
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_CAPABILITY_REQUIRED")
    state = _STATES.get(value)
    if state is None or config != state.config:
        _fail("PHYSICAL_FULL_MATRIX_V2_ACK_CHAIN_PROVENANCE_MISSING")
    expected = _derive(config=config, inputs=state.inputs, now=now)
    return _assert_result(value, expected=expected)
