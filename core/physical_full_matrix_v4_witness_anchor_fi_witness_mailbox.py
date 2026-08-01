"""Default-off V4-only WA-FI↔Witness anchor-mailbox foundation.

This module is deliberately a narrow transport boundary for the existing V4
Witness-anchor adapter.  It contains no provider SDK, network client, V2
mailbox/wire import, credential reader, route selector, controller handoff,
or secondary-site lane.  Four injected *named* role-local Object-Storage callbacks are
the only external surface:

* WA-FI controller request outbox → Witness request ingress; and
* Witness response outbox → WA-FI controller response inbox.

The bytes that cross those callbacks are either an existing canonical V4
controller append request or an existing V4 signed transport envelope.  The
envelope is re-verified here so a response always contains the permanent
immutable head/genesis layer and a distinct fresh challenge-bound observation.
Each enabled endpoint additionally requires its own injected, root-local V4
anti-replay registry.  It is reserved before every relevant role-local
callback boundary; there is no process-local replay fallback.  Nothing in this
module provisions a registry/checkpoint or callback, starts a campaign, or
authorizes execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import threading
from typing import Protocol

from core import physical_full_matrix_v4_witness_anchor_fi_witness_anti_replay_registry as _anti_replay
from core import physical_full_matrix_v4_witness_anchor_wire as _wire


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_DEFAULT_ENABLED",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_SCHEMA",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxClock",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxDispatcherConfig",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxPublicationReceipt",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponsePublicationReceipt",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransport",
    "PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransportConfig",
    "PhysicalFullMatrixV4WitnessAnchorWaFiAntiReplayRegistry",
    "PhysicalFullMatrixV4WitnessAnchorWaFiRequestOutbox",
    "PhysicalFullMatrixV4WitnessAnchorWaFiResponseInbox",
    "PhysicalFullMatrixV4WitnessAnchorWitnessAnchorService",
    "PhysicalFullMatrixV4WitnessAnchorWitnessAntiReplayRegistry",
    "PhysicalFullMatrixV4WitnessAnchorWitnessFiRequestIngress",
    "PhysicalFullMatrixV4WitnessAnchorWitnessFiResponseOutbox",
    "PhysicalFullMatrixV4WitnessAnchorWitnessFiMailboxDispatcher",
)


PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-fi-witness-mailbox-v1"
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_DEFAULT_ENABLED = False

_VERSION = 1
_WA_FI = "wa-fi"
_WITNESS = "witness"
_LANE = "v4-witness-anchor-wa-fi-witness"
_READ = "read-signed-head"
_APPEND = "append-signed-request"
_REQUEST_PREFIX = "physical-full-matrix-v4-witness-anchor/wa-fi-to-witness/requests/"
_RESPONSE_PREFIX = "physical-full-matrix-v4-witness-anchor/witness-to-wa-fi/responses/"
_IMMUTABLE_PREFIX = "physical-full-matrix-v4-witness-anchor/witness-immutable-records/"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_VERSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,255}$", re.ASCII)
_REQUEST_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-fi-witness-mailbox-request-v1"
)
_RESPONSE_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-fi-witness-mailbox-response-v1"
)
_RECEIPT_SCHEMA = (
    "gold-trade-physical-full-matrix-v4-witness-anchor-fi-witness-mailbox-receipt-v1"
)
_WA_FI_ANTI_REPLAY_STATE_NAMESPACE = "wa-fi-controller"
_WA_FI_ANTI_REPLAY_RESERVATION_PREFIX = "wa-fi-controller-v4-anchor-reservation"
_WITNESS_ANTI_REPLAY_STATE_NAMESPACE = "witness"
_WITNESS_ANTI_REPLAY_RESERVATION_PREFIX = "witness-v4-anchor-reservation"


class PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(RuntimeError):
    """The narrow FI↔Witness V4 mailbox boundary rejected unsafe evidence."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(code)


class PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxClock(Protocol):
    """Root-owned UTC clock injected into both non-live endpoints."""

    def now_utc(self) -> datetime: ...


class PhysicalFullMatrixV4WitnessAnchorWaFiAntiReplayRegistry(Protocol):
    """WA-FI-controller-local durable reservation seam for this exact lane."""

    def reserve_before_external_boundary(
        self,
        *,
        policy_identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        identifier_kind: str,
        identifier: str,
    ) -> _anti_replay.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationReceipt: ...


class PhysicalFullMatrixV4WitnessAnchorWitnessAntiReplayRegistry(Protocol):
    """Witness-local durable reservation seam for this exact FI lane."""

    def reserve_before_external_boundary(
        self,
        *,
        policy_identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        identifier_kind: str,
        identifier: str,
    ) -> _anti_replay.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationReceipt: ...


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy:
    """Exact public FI↔Witness lane pins; no endpoint or credential is present."""

    verification_policy: _wire.PhysicalFullMatrixV4WitnessAnchorVerificationPolicy | None = field(
        default=None,
        repr=False,
    )
    policy_identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity | None = field(
        default=None,
        repr=False,
    )
    controller_site: str = _WA_FI
    witness_site: str = _WITNESS
    lane: str = _LANE
    wa_fi_request_prefix: str = _REQUEST_PREFIX
    witness_response_prefix: str = _RESPONSE_PREFIX
    witness_immutable_record_prefix: str = _IMMUTABLE_PREFIX
    wa_fi_request_bucket_sha256: str = ""
    wa_fi_response_bucket_sha256: str = ""
    witness_immutable_record_bucket_sha256: str = ""
    wa_fi_request_outbox_iam_sha256: str = ""
    witness_fi_request_ingress_iam_sha256: str = ""
    witness_fi_response_outbox_iam_sha256: str = ""
    wa_fi_response_inbox_iam_sha256: str = ""
    request_object_lock_sha256: str = ""
    response_object_lock_sha256: str = ""
    immutable_record_object_lock_sha256: str = ""

    def __reduce_ex__(self, protocol: int):
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_POLICY_NON_SERIALIZABLE")


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest:
    """Non-authorizing mailbox wrapper around one existing V4 operation."""

    schema: str
    operation: str
    policy_identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity
    read_challenge: str
    request_sha256: str
    canonical_controller_append_request: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse:
    """Correlation wrapper whose payload is exactly an existing V4 envelope."""

    schema: str
    operation: str
    policy_identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity
    read_challenge: str
    request_sha256: str
    canonical_transport_envelope: bytes = field(repr=False)
    response_sha256: str = ""


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxPublicationReceipt:
    """Create-only request publication receipt from the WA-FI local callback."""

    schema: str
    request_sha256: str
    read_challenge: str
    object_version_id: str
    receipt_sha256: str


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponsePublicationReceipt:
    """Create-only response publication receipt from the Witness local callback."""

    schema: str
    request_sha256: str
    read_challenge: str
    object_version_id: str
    receipt_sha256: str


class PhysicalFullMatrixV4WitnessAnchorWaFiRequestOutbox(Protocol):
    """WA-FI-only publisher; it has no Witness or secondary-site reader capability."""

    def publish_wa_fi_v4_witness_anchor_request(
        self,
        *,
        root_policy: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
        request: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest,
    ) -> PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxPublicationReceipt: ...


class PhysicalFullMatrixV4WitnessAnchorWaFiResponseInbox(Protocol):
    """WA-FI-only exact response consumer; it cannot read Witness records."""

    def consume_wa_fi_v4_witness_anchor_response(
        self,
        *,
        root_policy: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
        request_sha256: str,
        read_challenge: str,
    ) -> PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse: ...


class PhysicalFullMatrixV4WitnessAnchorWitnessFiRequestIngress(Protocol):
    """Witness-only FI lane request consumer; it cannot select another route."""

    def consume_witness_fi_v4_witness_anchor_request(
        self,
        *,
        root_policy: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
    ) -> PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest | None: ...


class PhysicalFullMatrixV4WitnessAnchorWitnessFiResponseOutbox(Protocol):
    """Witness-only FI lane response publisher; it has no controller credential."""

    def publish_witness_fi_v4_witness_anchor_response(
        self,
        *,
        root_policy: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
        response: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse,
    ) -> PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponsePublicationReceipt: ...


class PhysicalFullMatrixV4WitnessAnchorWitnessAnchorService(Protocol):
    """The existing narrow Witness service shape, not a generic RPC surface."""

    def read_signed_head(
        self,
        *,
        policy_identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        read_challenge: str,
    ) -> bytes: ...

    def append_signed_request(
        self,
        *,
        policy_identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        canonical_controller_append_request: bytes,
        read_challenge: str,
    ) -> bytes: ...


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransportConfig:
    """Default-off WA-FI endpoint with two callbacks and one local registry.

    ``wa_fi_anti_replay_registry`` is deliberately mandatory whenever this
    endpoint is enabled.  The mailbox never substitutes its former
    process-local replay sets for the root-local reservation state.
    """

    root_policy: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy | None = field(
        default=None,
        repr=False,
    )
    clock: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxClock | None = field(
        default=None,
        repr=False,
    )
    wa_fi_request_outbox: PhysicalFullMatrixV4WitnessAnchorWaFiRequestOutbox | None = field(
        default=None,
        repr=False,
    )
    wa_fi_response_inbox: PhysicalFullMatrixV4WitnessAnchorWaFiResponseInbox | None = field(
        default=None,
        repr=False,
    )
    wa_fi_anti_replay_registry: PhysicalFullMatrixV4WitnessAnchorWaFiAntiReplayRegistry | None = field(
        default=None,
        repr=False,
    )
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_DEFAULT_ENABLED

    def __reduce_ex__(self, protocol: int):
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_TRANSPORT_CONFIG_NON_SERIALIZABLE")


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxDispatcherConfig:
    """Default-off Witness FI-lane endpoint with callbacks and local registry.

    ``witness_anti_replay_registry`` is deliberately mandatory whenever this
    endpoint is enabled.  It must be the Witness role's distinct local
    namespace, never the WA-FI controller registry.
    """

    root_policy: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy | None = field(
        default=None,
        repr=False,
    )
    clock: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxClock | None = field(
        default=None,
        repr=False,
    )
    witness_fi_request_ingress: PhysicalFullMatrixV4WitnessAnchorWitnessFiRequestIngress | None = field(
        default=None,
        repr=False,
    )
    witness_fi_response_outbox: PhysicalFullMatrixV4WitnessAnchorWitnessFiResponseOutbox | None = field(
        default=None,
        repr=False,
    )
    witness_anchor_service: PhysicalFullMatrixV4WitnessAnchorWitnessAnchorService | None = field(
        default=None,
        repr=False,
    )
    witness_anti_replay_registry: PhysicalFullMatrixV4WitnessAnchorWitnessAntiReplayRegistry | None = field(
        default=None,
        repr=False,
    )
    enabled: bool = PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_DEFAULT_ENABLED

    def __reduce_ex__(self, protocol: int):
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_DISPATCHER_CONFIG_NON_SERIALIZABLE")


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
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(code) from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _challenge(value: object, *, code: str) -> str:
    return _sha256(value, code=code)


def _utc(value: object, *, code: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _now(clock: object) -> datetime:
    try:
        callback = clock.now_utc  # type: ignore[attr-defined]
        value = callback()
    except Exception as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_CLOCK_INVALID"
        ) from exc
    return _utc(
        value,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_CLOCK_INVALID",
    )


def _identity_mapping(
    value: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
) -> dict[str, object]:
    return {
        "schema": value.schema,
        "journal_binding_sha256": value.journal_binding_sha256,
        "baseline_plan_binding_sha256": value.baseline_plan_binding_sha256,
        "run_id": str(value.run_id),
        "plan_sha256": value.plan_sha256,
        "anchor_genesis_sequence": value.anchor_genesis_sequence,
        "anchor_genesis_head_sha256": value.anchor_genesis_head_sha256,
        "canonical_genesis_sha256": value.canonical_genesis_sha256,
    }


def _anti_replay_identity_sha256(
    identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
) -> str:
    """Match the reviewed registry's canonical, newline-terminated identity."""

    return _sha256_bytes(
        _canonical(
            _identity_mapping(identity),
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_ANTI_REPLAY_RECEIPT_INVALID",
        )
        + b"\n"
    )


def _reserve_anti_replay_identifier(
    *,
    registry: object,
    role: str,
    policy_identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    identifier_kind: str,
    identifier: str,
) -> None:
    """Durably burn one exact identifier before a role-local callback.

    The registry performs the actual persistence and root-owned rollback
    checkpoint.  This adapter only admits its exact receipt shape and closed
    endpoint role.  Any error is terminal for this mailbox attempt: callers
    deliberately have no local fallback or retry release mechanism.
    """

    if role == _WA_FI:
        expected_role = _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WA_FI_CONTROLLER
        expected_namespace = _WA_FI_ANTI_REPLAY_STATE_NAMESPACE
        expected_prefix = _WA_FI_ANTI_REPLAY_RESERVATION_PREFIX
    elif role == _WITNESS:
        expected_role = _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ROLE_WITNESS
        expected_namespace = _WITNESS_ANTI_REPLAY_STATE_NAMESPACE
        expected_prefix = _WITNESS_ANTI_REPLAY_RESERVATION_PREFIX
    else:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_ANTI_REPLAY_ROLE_INVALID")
    if identifier_kind not in {
        _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_READ_CHALLENGE,
        _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_CONTROLLER_REPLAY_ID,
        _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_WITNESS_OBSERVATION_ID,
    }:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_ANTI_REPLAY_IDENTIFIER_INVALID")
    checked_identifier = _sha256(
        identifier,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_ANTI_REPLAY_IDENTIFIER_INVALID",
    )
    callback = getattr(registry, "reserve_before_external_boundary", None)
    if not callable(callback):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_ANTI_REPLAY_REGISTRY_INVALID")
    try:
        receipt = callback(
            policy_identity=policy_identity,
            identifier_kind=identifier_kind,
            identifier=checked_identifier,
        )
    except Exception as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_ANTI_REPLAY_RESERVATION_FAILED"
        ) from exc
    if (
        type(receipt)
        is not _anti_replay.PhysicalFullMatrixV4WitnessAnchorFiWitnessAntiReplayReservationReceipt
        or receipt.schema
        != _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_ANTI_REPLAY_SCHEMA
        or receipt.role != expected_role
        or receipt.state_namespace != expected_namespace
        or receipt.reservation_prefix != expected_prefix
        or receipt.policy_identity_sha256 != _anti_replay_identity_sha256(policy_identity)
        or receipt.identifier_kind != identifier_kind
        or receipt.identifier != checked_identifier
        or type(receipt.reservation_sequence) is not int
        or receipt.reservation_sequence <= 0
        or _sha256(
            receipt.reservation_record_sha256,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_ANTI_REPLAY_RECEIPT_INVALID",
        )
        != receipt.reservation_record_sha256
        or receipt.execution_authorized is not False
        or receipt.promotion_authorized is not False
        or receipt.full_matrix_executed is not False
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_ANTI_REPLAY_RECEIPT_INVALID")


def _expected_identity(
    policy: _wire.PhysicalFullMatrixV4WitnessAnchorVerificationPolicy,
    *,
    now: datetime,
) -> _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity:
    try:
        genesis = _wire.verified_physical_full_matrix_v4_witness_anchor_genesis_head(
            policy=policy,
            now=now,
        )
        canonical_genesis = (
            _wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(
                policy.genesis
            )
        )
    except _wire.PhysicalFullMatrixV4WitnessAnchorWireError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_POLICY_INVALID"
        ) from exc
    return _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity(
        schema=_wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_IDENTITY_SCHEMA,
        journal_binding_sha256=genesis.journal_binding_sha256,
        baseline_plan_binding_sha256=genesis.baseline_plan_binding_sha256,
        run_id=genesis.run_id,
        plan_sha256=genesis.plan_sha256,
        anchor_genesis_sequence=genesis.sequence,
        anchor_genesis_head_sha256=genesis.head_sha256,
        canonical_genesis_sha256=_sha256_bytes(canonical_genesis),
    )


def _root_policy(
    value: object,
    *,
    now: datetime,
) -> PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy:
    if type(value) is not PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_POLICY_INVALID")
    if (
        type(value.verification_policy)
        is not _wire.PhysicalFullMatrixV4WitnessAnchorVerificationPolicy
        or type(value.policy_identity)
        is not _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity
        or value.controller_site != _WA_FI
        or value.witness_site != _WITNESS
        or value.lane != _LANE
        or value.wa_fi_request_prefix != _REQUEST_PREFIX
        or value.witness_response_prefix != _RESPONSE_PREFIX
        or value.witness_immutable_record_prefix != _IMMUTABLE_PREFIX
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_POLICY_INVALID")
    for pin in (
        value.wa_fi_request_bucket_sha256,
        value.wa_fi_response_bucket_sha256,
        value.witness_immutable_record_bucket_sha256,
        value.wa_fi_request_outbox_iam_sha256,
        value.witness_fi_request_ingress_iam_sha256,
        value.witness_fi_response_outbox_iam_sha256,
        value.wa_fi_response_inbox_iam_sha256,
        value.request_object_lock_sha256,
        value.response_object_lock_sha256,
        value.immutable_record_object_lock_sha256,
    ):
        _sha256(
            pin,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_POLICY_INVALID",
        )
    expected = _expected_identity(value.verification_policy, now=now)
    if value.policy_identity != expected:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_POLICY_IDENTITY_MISMATCH")
    return value


def _identity(
    value: object,
    *,
    expected: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    code: str,
) -> _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity:
    if (
        type(value) is not _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity
        or value != expected
    ):
        _fail(code)
    return value


def _request_digest(
    *,
    operation: str,
    identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    read_challenge: str,
    canonical_controller_append_request: bytes | None,
) -> str:
    if operation == _READ:
        if canonical_controller_append_request is not None:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_INVALID")
        return _sha256_bytes(
            _canonical(
                {
                    "schema": _REQUEST_SCHEMA,
                    "operation": _READ,
                    "policy_identity": _identity_mapping(identity),
                    "read_challenge": read_challenge,
                },
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_INVALID",
            )
        )
    if operation == _APPEND and type(canonical_controller_append_request) is bytes:
        return _sha256_bytes(canonical_controller_append_request)
    _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_INVALID")


def _append_request_fresh_and_pinned(
    value: bytes,
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
    now: datetime,
    code: str,
) -> _wire.PhysicalFullMatrixV4WitnessAnchorControllerAppendRequest:
    try:
        parsed = _wire.parse_physical_full_matrix_v4_witness_anchor_controller_append_request(
            value
        )
    except _wire.PhysicalFullMatrixV4WitnessAnchorWireError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(code) from exc
    expected = policy.policy_identity
    if (
        parsed.journal_binding_sha256 != expected.journal_binding_sha256
        or parsed.baseline_plan_binding_sha256
        != expected.baseline_plan_binding_sha256
        or parsed.run_id != expected.run_id
        or parsed.plan_sha256 != expected.plan_sha256
        or parsed.anchor_genesis_sequence != expected.anchor_genesis_sequence
        or parsed.anchor_genesis_head_sha256 != expected.anchor_genesis_head_sha256
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_IDENTITY_MISMATCH")
    verification = policy.verification_policy
    assert verification is not None
    if (
        parsed.expires_at < parsed.issued_at
        or parsed.expires_at - parsed.issued_at
        > timedelta(seconds=verification.maximum_request_lifetime_seconds)
        or parsed.issued_at
        > now + timedelta(seconds=verification.maximum_future_skew_seconds)
        or parsed.expires_at < now
        or parsed.commitment.occurred_at
        > parsed.issued_at
        + timedelta(seconds=verification.maximum_future_skew_seconds)
        or parsed.commitment.occurred_at > parsed.expires_at
        or now - parsed.commitment.occurred_at
        > timedelta(seconds=verification.maximum_commitment_age_seconds)
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_EXPIRED")
    return parsed


def _request(
    value: object,
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
    now: datetime,
) -> PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest:
    if type(value) is not PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_INVALID")
    if value.schema != _REQUEST_SCHEMA or value.operation not in {_READ, _APPEND}:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_INVALID")
    identity = _identity(
        value.policy_identity,
        expected=policy.policy_identity,  # type: ignore[arg-type]
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_IDENTITY_MISMATCH",
    )
    challenge = _challenge(
        value.read_challenge,
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_INVALID",
    )
    if value.operation == _READ:
        append_bytes = value.canonical_controller_append_request
    else:
        append_bytes = value.canonical_controller_append_request
        if type(append_bytes) is not bytes:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_INVALID")
        _append_request_fresh_and_pinned(
            append_bytes,
            policy=policy,
            now=now,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_INVALID",
        )
    if value.request_sha256 != _request_digest(
        operation=value.operation,
        identity=identity,
        read_challenge=challenge,
        canonical_controller_append_request=append_bytes,
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_CORRELATION_MISMATCH")
    return value


def _response_digest(
    *,
    operation: str,
    identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    read_challenge: str,
    request_sha256: str,
    canonical_transport_envelope: bytes,
) -> str:
    return _sha256_bytes(
        _canonical(
            {
                "schema": _RESPONSE_SCHEMA,
                "operation": operation,
                "policy_identity": _identity_mapping(identity),
                "read_challenge": read_challenge,
                "request_sha256": request_sha256,
                "canonical_transport_envelope_sha256": _sha256_bytes(
                    canonical_transport_envelope
                ),
            },
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_RESPONSE_INVALID",
        )
    )


def _response(
    value: object,
    *,
    policy: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
    now: datetime,
    operation: str,
    request_sha256: str,
    read_challenge: str,
) -> tuple[PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse, str]:
    if type(value) is not PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_AMBIGUOUS_RESPONSE")
    if (
        value.schema != _RESPONSE_SCHEMA
        or value.operation != operation
        or value.request_sha256 != request_sha256
        or value.read_challenge != read_challenge
        or type(value.canonical_transport_envelope) is not bytes
        or value.response_sha256
        != _response_digest(
            operation=operation,
            identity=policy.policy_identity,  # type: ignore[arg-type]
            read_challenge=read_challenge,
            request_sha256=request_sha256,
            canonical_transport_envelope=value.canonical_transport_envelope,
        )
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_RESPONSE_CORRELATION_MISMATCH")
    _identity(
        value.policy_identity,
        expected=policy.policy_identity,  # type: ignore[arg-type]
        code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_RESPONSE_IDENTITY_MISMATCH",
    )
    try:
        verified = _wire.verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
            value.canonical_transport_envelope,
            policy=policy.verification_policy,  # type: ignore[arg-type]
            now=now,
            expected_read_challenge=read_challenge,
            # Durable replay detection is performed immediately after this
            # cryptographic verification by the role-local registry.  Do not
            # silently fall back to process-local restart-unsafe state here.
            seen_observation_ids=frozenset(),
        )
    except _wire.PhysicalFullMatrixV4WitnessAnchorWireError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_RESPONSE_INVALID"
        ) from exc
    # A fresh envelope alone is not sufficient for an append response.  Its
    # immutable layer must be the precise head created from the exact existing
    # controller request bytes carried by this mailbox request.  This closes
    # substitution of a valid, fresh observation for an unrelated append.
    if (
        operation == _APPEND
        and (
            type(verified.anchor_head)
            is not _wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
            or verified.anchor_head.controller_request_sha256 != request_sha256
        )
    ):
        _fail(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_RESPONSE_CORRELATION_MISMATCH"
        )
    # The V4 verifier above proves both canonical layers.  The caller must now
    # durably reserve this observation before it publishes or returns it.
    return value, verified.read_observation.observation_id


def _receipt_digest(
    *,
    request_sha256: str,
    read_challenge: str,
    object_version_id: str,
) -> str:
    return _sha256_bytes(
        _canonical(
            {
                "schema": _RECEIPT_SCHEMA,
                "request_sha256": request_sha256,
                "read_challenge": read_challenge,
                "object_version_id": object_version_id,
            },
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_RECEIPT_INVALID",
        )
    )


def _publication_receipt(
    value: object,
    *,
    request_sha256: str,
    read_challenge: str,
    response: bool,
) -> None:
    expected_type = (
        PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponsePublicationReceipt
        if response
        else PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxPublicationReceipt
    )
    if type(value) is not expected_type:
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_RECEIPT_INVALID")
    receipt = value
    if (
        receipt.schema != _RECEIPT_SCHEMA
        or receipt.request_sha256 != request_sha256
        or receipt.read_challenge != read_challenge
        or type(receipt.object_version_id) is not str
        or _VERSION_ID_RE.fullmatch(receipt.object_version_id) is None
        or receipt.receipt_sha256
        != _receipt_digest(
            request_sha256=request_sha256,
            read_challenge=read_challenge,
            object_version_id=receipt.object_version_id,
        )
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_RECEIPT_INVALID")


def _transport_config(
    value: object,
) -> tuple[
    PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
    PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxClock,
    PhysicalFullMatrixV4WitnessAnchorWaFiRequestOutbox,
    PhysicalFullMatrixV4WitnessAnchorWaFiResponseInbox,
    PhysicalFullMatrixV4WitnessAnchorWaFiAntiReplayRegistry,
]:
    if (
        type(value) is not PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransportConfig
        or value.enabled is not True
        or value.clock is None
        or value.wa_fi_request_outbox is None
        or value.wa_fi_response_inbox is None
        or value.wa_fi_anti_replay_registry is None
        or not callable(
            getattr(value.wa_fi_anti_replay_registry, "reserve_before_external_boundary", None)
        )
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_TRANSPORT_CONFIG_INVALID")
    now = _now(value.clock)
    return (
        _root_policy(value.root_policy, now=now),
        value.clock,
        value.wa_fi_request_outbox,
        value.wa_fi_response_inbox,
        value.wa_fi_anti_replay_registry,
    )


def _dispatcher_config(
    value: object,
) -> tuple[
    PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRootPolicy,
    PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxClock,
    PhysicalFullMatrixV4WitnessAnchorWitnessFiRequestIngress,
    PhysicalFullMatrixV4WitnessAnchorWitnessFiResponseOutbox,
    PhysicalFullMatrixV4WitnessAnchorWitnessAnchorService,
    PhysicalFullMatrixV4WitnessAnchorWitnessAntiReplayRegistry,
]:
    if (
        type(value) is not PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxDispatcherConfig
        or value.enabled is not True
        or value.clock is None
        or value.witness_fi_request_ingress is None
        or value.witness_fi_response_outbox is None
        or value.witness_anchor_service is None
        or value.witness_anti_replay_registry is None
        or not callable(
            getattr(value.witness_anti_replay_registry, "reserve_before_external_boundary", None)
        )
    ):
        _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_DISPATCHER_CONFIG_INVALID")
    now = _now(value.clock)
    return (
        _root_policy(value.root_policy, now=now),
        value.clock,
        value.witness_fi_request_ingress,
        value.witness_fi_response_outbox,
        value.witness_anchor_service,
        value.witness_anti_replay_registry,
    )


class PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransport:
    """Adapter-facing exact WA-FI→Witness→WA-FI mailbox implementation."""

    def __init__(
        self,
        *,
        config: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxTransportConfig,
    ) -> None:
        policy, clock, outbox, inbox, anti_replay_registry = _transport_config(config)
        self._policy = policy
        self._clock = clock
        self._outbox = outbox
        self._inbox = inbox
        self._anti_replay_registry = anti_replay_registry
        self._lock = threading.RLock()

    def __reduce_ex__(self, protocol: int):
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_TRANSPORT_NON_SERIALIZABLE")

    def _dispatch_and_receive(
        self,
        *,
        request: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest,
    ) -> bytes:
        try:
            receipt = self._outbox.publish_wa_fi_v4_witness_anchor_request(
                root_policy=self._policy,
                request=request,
            )
            _publication_receipt(
                receipt,
                request_sha256=request.request_sha256,
                read_challenge=request.read_challenge,
                response=False,
            )
        except PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError:
            raise
        except Exception as exc:
            raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_PUBLICATION_FAILED"
            ) from exc
        try:
            response = self._inbox.consume_wa_fi_v4_witness_anchor_response(
                root_policy=self._policy,
                request_sha256=request.request_sha256,
                read_challenge=request.read_challenge,
            )
        except PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError:
            raise
        except Exception as exc:
            raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_AMBIGUOUS_RESPONSE"
            ) from exc
        verified_response, observation_id = _response(
            response,
            policy=self._policy,
            now=_now(self._clock),
            operation=request.operation,
            request_sha256=request.request_sha256,
            read_challenge=request.read_challenge,
        )
        _reserve_anti_replay_identifier(
            registry=self._anti_replay_registry,
            role=_WA_FI,
            policy_identity=self._policy.policy_identity,  # type: ignore[arg-type]
            identifier_kind=(
                _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_WITNESS_OBSERVATION_ID
            ),
            identifier=observation_id,
        )
        return verified_response.canonical_transport_envelope

    def read_signed_head(
        self,
        *,
        policy_identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        read_challenge: str,
    ) -> bytes:
        """Publish one challenge-bound V4 read request and return one envelope."""

        with self._lock:
            _identity(
                policy_identity,
                expected=self._policy.policy_identity,  # type: ignore[arg-type]
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_POLICY_IDENTITY_MISMATCH",
            )
            challenge = _challenge(
                read_challenge,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_READ_CHALLENGE_INVALID",
            )
            # Reserve before publication.  A timeout is intentionally not
            # retryable; the registry has no release operation.
            _reserve_anti_replay_identifier(
                registry=self._anti_replay_registry,
                role=_WA_FI,
                policy_identity=self._policy.policy_identity,  # type: ignore[arg-type]
                identifier_kind=(
                    _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_READ_CHALLENGE
                ),
                identifier=challenge,
            )
            request = PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest(
                schema=_REQUEST_SCHEMA,
                operation=_READ,
                policy_identity=self._policy.policy_identity,  # type: ignore[arg-type]
                read_challenge=challenge,
                request_sha256=_request_digest(
                    operation=_READ,
                    identity=self._policy.policy_identity,  # type: ignore[arg-type]
                    read_challenge=challenge,
                    canonical_controller_append_request=None,
                ),
            )
            return self._dispatch_and_receive(request=request)

    def append_signed_request(
        self,
        *,
        policy_identity: _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        canonical_controller_append_request: bytes,
        read_challenge: str,
    ) -> bytes:
        """Publish one existing V4 signed append request; never retries it."""

        with self._lock:
            _identity(
                policy_identity,
                expected=self._policy.policy_identity,  # type: ignore[arg-type]
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_POLICY_IDENTITY_MISMATCH",
            )
            challenge = _challenge(
                read_challenge,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_READ_CHALLENGE_INVALID",
            )
            if type(canonical_controller_append_request) is not bytes:
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_INVALID")
            parsed = _append_request_fresh_and_pinned(
                canonical_controller_append_request,
                policy=self._policy,
                now=_now(self._clock),
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_INVALID",
            )
            # Both values are durably reserved before the external publication
            # boundary.  If the second reservation fails, the first remains
            # burned rather than being retried locally.
            _reserve_anti_replay_identifier(
                registry=self._anti_replay_registry,
                role=_WA_FI,
                policy_identity=self._policy.policy_identity,  # type: ignore[arg-type]
                identifier_kind=(
                    _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_READ_CHALLENGE
                ),
                identifier=challenge,
            )
            _reserve_anti_replay_identifier(
                registry=self._anti_replay_registry,
                role=_WA_FI,
                policy_identity=self._policy.policy_identity,  # type: ignore[arg-type]
                identifier_kind=(
                    _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_CONTROLLER_REPLAY_ID
                ),
                identifier=parsed.replay_id,
            )
            request = PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxRequest(
                schema=_REQUEST_SCHEMA,
                operation=_APPEND,
                policy_identity=self._policy.policy_identity,  # type: ignore[arg-type]
                read_challenge=challenge,
                request_sha256=_request_digest(
                    operation=_APPEND,
                    identity=self._policy.policy_identity,  # type: ignore[arg-type]
                    read_challenge=challenge,
                    canonical_controller_append_request=canonical_controller_append_request,
                ),
                canonical_controller_append_request=canonical_controller_append_request,
            )
            return self._dispatch_and_receive(request=request)


class PhysicalFullMatrixV4WitnessAnchorWitnessFiMailboxDispatcher:
    """Witness FI-lane dispatcher; one request becomes at most one response."""

    def __init__(
        self,
        *,
        config: PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxDispatcherConfig,
    ) -> None:
        policy, clock, ingress, outbox, service, anti_replay_registry = _dispatcher_config(config)
        self._policy = policy
        self._clock = clock
        self._ingress = ingress
        self._outbox = outbox
        self._service = service
        self._anti_replay_registry = anti_replay_registry
        self._lock = threading.RLock()

    def __reduce_ex__(self, protocol: int):
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_DISPATCHER_NON_SERIALIZABLE")

    def dispatch_one(self) -> PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse | None:
        """Consume at most one FI request and emit one exact V4 envelope response."""

        with self._lock:
            try:
                incoming = self._ingress.consume_witness_fi_v4_witness_anchor_request(
                    root_policy=self._policy
                )
            except Exception as exc:
                raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(
                    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_CONSUME_FAILED"
                ) from exc
            if incoming is None:
                return None
            now = _now(self._clock)
            request = _request(incoming, policy=self._policy, now=now)
            # The parsed exact request challenge is permanently unavailable
            # before the service sees the request or a response can be made.
            _reserve_anti_replay_identifier(
                registry=self._anti_replay_registry,
                role=_WITNESS,
                policy_identity=self._policy.policy_identity,  # type: ignore[arg-type]
                identifier_kind=(
                    _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_READ_CHALLENGE
                ),
                identifier=request.read_challenge,
            )
            if request.operation == _READ:
                try:
                    envelope = self._service.read_signed_head(
                        policy_identity=self._policy.policy_identity,  # type: ignore[arg-type]
                        read_challenge=request.read_challenge,
                    )
                except Exception as exc:
                    raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(
                        "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_WITNESS_READ_FAILED"
                    ) from exc
            else:
                assert request.canonical_controller_append_request is not None
                parsed = _append_request_fresh_and_pinned(
                    request.canonical_controller_append_request,
                    policy=self._policy,
                    now=now,
                    code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_REQUEST_INVALID",
                )
                _reserve_anti_replay_identifier(
                    registry=self._anti_replay_registry,
                    role=_WITNESS,
                    policy_identity=self._policy.policy_identity,  # type: ignore[arg-type]
                    identifier_kind=(
                        _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_CONTROLLER_REPLAY_ID
                    ),
                    identifier=parsed.replay_id,
                )
                try:
                    envelope = self._service.append_signed_request(
                        policy_identity=self._policy.policy_identity,  # type: ignore[arg-type]
                        canonical_controller_append_request=request.canonical_controller_append_request,
                        read_challenge=request.read_challenge,
                    )
                except Exception as exc:
                    raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(
                        "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_WITNESS_APPEND_FAILED"
                    ) from exc
            response = PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxResponse(
                schema=_RESPONSE_SCHEMA,
                operation=request.operation,
                policy_identity=self._policy.policy_identity,  # type: ignore[arg-type]
                read_challenge=request.read_challenge,
                request_sha256=request.request_sha256,
                canonical_transport_envelope=envelope,
                response_sha256=_response_digest(
                    operation=request.operation,
                    identity=self._policy.policy_identity,  # type: ignore[arg-type]
                    read_challenge=request.read_challenge,
                    request_sha256=request.request_sha256,
                    canonical_transport_envelope=envelope,
                ),
            )
            _, observation_id = _response(
                response,
                policy=self._policy,
                now=_now(self._clock),
                operation=request.operation,
                request_sha256=request.request_sha256,
                read_challenge=request.read_challenge,
            )
            # Verify the signed envelope first, then durably reserve its
            # observation before the create-only response publication.  A
            # publication ambiguity cannot be retried with that observation.
            _reserve_anti_replay_identifier(
                registry=self._anti_replay_registry,
                role=_WITNESS,
                policy_identity=self._policy.policy_identity,  # type: ignore[arg-type]
                identifier_kind=(
                    _anti_replay.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_IDENTIFIER_KIND_WITNESS_OBSERVATION_ID
                ),
                identifier=observation_id,
            )
            try:
                receipt = self._outbox.publish_witness_fi_v4_witness_anchor_response(
                    root_policy=self._policy,
                    response=response,
                )
                _publication_receipt(
                    receipt,
                    request_sha256=request.request_sha256,
                    read_challenge=request.read_challenge,
                    response=True,
                )
            except PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError:
                raise
            except Exception as exc:
                raise PhysicalFullMatrixV4WitnessAnchorFiWitnessMailboxError(
                    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_FI_WITNESS_MAILBOX_RESPONSE_PUBLICATION_FAILED"
                ) from exc
            return response
