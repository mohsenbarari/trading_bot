"""Root-only V4 receipt-journal adapter for the signed Witness Wire V2.

The journal deliberately contains no network, ledger, provider, or signer
transport.  This is its only signed-Wire bridge.  Its injected transport has
exactly two operations: read one current signed envelope, or submit one
controller-signed append request and receive one signed envelope.

Every returned envelope contains two different facts:

* a permanent, signed immutable genesis/append record; and
* a short-lived, challenge-bound signed read observation of that exact record.

Only the permanent record is mapped to the journal and persisted there.  A
fresh observation is mandatory before every mapping, so it can expire and be
renewed without changing the immutable journal tail.  There is deliberately
no local commitment/hash projection.

The journal passes its durable expected tail on every read.  The adapter/Wire
will accept only that exact immutable current head or an exact immediate
successor (for the externally-committed/local-record-pending crash case).
The journal then remains the final create-only local-tail and pending-state
barrier.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
import threading
from typing import Protocol
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core import physical_full_matrix_v4_receipt_journal as _journal
from core import physical_full_matrix_v4_witness_anchor_wire as _wire


__all__ = (
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_DEFAULT_REQUEST_LIFETIME_SECONDS",
    "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_IDENTITY_SCHEMA",
    "PhysicalFullMatrixV4WitnessAnchorAdapterClock",
    "PhysicalFullMatrixV4WitnessAnchorAdapterConfig",
    "PhysicalFullMatrixV4WitnessAnchorAdapterError",
    "PhysicalFullMatrixV4WitnessAnchorPolicyIdentity",
    "PhysicalFullMatrixV4WitnessAnchorReadChallengeSource",
    "PhysicalFullMatrixV4WitnessAnchorReplayIdSource",
    "PhysicalFullMatrixV4WitnessAnchorSignedWireTransport",
    "PhysicalFullMatrixV4WitnessAnchorWireAdapter",
)


PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_IDENTITY_SCHEMA = (
    _wire.PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_POLICY_IDENTITY_SCHEMA
)
PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_DEFAULT_REQUEST_LIFETIME_SECONDS = 60

_ZERO_SHA256 = "0" * 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class PhysicalFullMatrixV4WitnessAnchorAdapterError(RuntimeError):
    """The signed-Wire adapter rejected an unsafe Witness interaction."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalFullMatrixV4WitnessAnchorAdapterError(code)


# Public compatibility name for the exact Wire-owned value object.  Keeping
# the type in Wire lets the root ledger validate it by exact type without an
# adapter↔ledger import cycle or duck-typed transport identity.
PhysicalFullMatrixV4WitnessAnchorPolicyIdentity = (
    _wire.PhysicalFullMatrixV4WitnessAnchorPolicyIdentity
)


class PhysicalFullMatrixV4WitnessAnchorSignedWireTransport(Protocol):
    """Narrow external boundary; it cannot dispatch arbitrary operations."""

    def read_signed_head(
        self,
        *,
        policy_identity: PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        read_challenge: str,
    ) -> bytes: ...

    def append_signed_request(
        self,
        *,
        policy_identity: PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
        canonical_controller_append_request: bytes,
        read_challenge: str,
    ) -> bytes: ...


class PhysicalFullMatrixV4WitnessAnchorAdapterClock(Protocol):
    """Injected trusted UTC clock; this adapter has no provider clock client."""

    def now_utc(self) -> datetime: ...


class PhysicalFullMatrixV4WitnessAnchorReplayIdSource(Protocol):
    """Supply a fresh 64-hex identifier for one controller append request."""

    def next_controller_append_replay_id(
        self,
        *,
        policy_identity: PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    ) -> str: ...


class PhysicalFullMatrixV4WitnessAnchorReadChallengeSource(Protocol):
    """Supply a fresh root-local 64-hex challenge for one read/response."""

    def next_witness_read_challenge(
        self,
        *,
        policy_identity: PhysicalFullMatrixV4WitnessAnchorPolicyIdentity,
    ) -> str: ...


@dataclass(frozen=True)
class PhysicalFullMatrixV4WitnessAnchorAdapterConfig:
    """Policy-pinned root-process dependencies; deliberately nonserializable."""

    policy: _wire.PhysicalFullMatrixV4WitnessAnchorVerificationPolicy
    controller_private_key: Ed25519PrivateKey = field(repr=False)
    transport: PhysicalFullMatrixV4WitnessAnchorSignedWireTransport
    clock: PhysicalFullMatrixV4WitnessAnchorAdapterClock
    replay_id_source: PhysicalFullMatrixV4WitnessAnchorReplayIdSource
    read_challenge_source: PhysicalFullMatrixV4WitnessAnchorReadChallengeSource
    request_lifetime_seconds: int = (
        PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_DEFAULT_REQUEST_LIFETIME_SECONDS
    )

    def __reduce_ex__(self, protocol: int):
        del protocol
        raise TypeError(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CONFIG_NON_SERIALIZABLE"
        )


_VerifiedAnchor = (
    _wire.VerifiedPhysicalFullMatrixV4WitnessAnchorHead
    | _wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
)


@dataclass(frozen=True)
class _ObservedAnchor:
    verified: _VerifiedAnchor
    canonical_anchor: bytes


def _as_utc(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(code)
    try:
        offset = value.utcoffset()
    except (OverflowError, ValueError) as exc:
        raise PhysicalFullMatrixV4WitnessAnchorAdapterError(code) from exc
    if offset is None:
        _fail(code)
    return value.astimezone(timezone.utc)


def _wire_error(code: str, callback):  # type: ignore[no-untyped-def]
    try:
        return callback()
    except _wire.PhysicalFullMatrixV4WitnessAnchorWireError as exc:
        raise PhysicalFullMatrixV4WitnessAnchorAdapterError(code) from exc


class PhysicalFullMatrixV4WitnessAnchorWireAdapter:
    """Implement the V4 journal anchor Protocol from verified Wire V2 only."""

    def __init__(self, *, config: PhysicalFullMatrixV4WitnessAnchorAdapterConfig) -> None:
        if type(config) is not PhysicalFullMatrixV4WitnessAnchorAdapterConfig:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CONFIG_INVALID")
        try:
            if os.geteuid() != 0:
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_ROOT_RUNTIME_REQUIRED")
        except (AttributeError, OSError) as exc:
            raise PhysicalFullMatrixV4WitnessAnchorAdapterError(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_ROOT_RUNTIME_REQUIRED"
            ) from exc
        if (
            type(config.policy)
            is not _wire.PhysicalFullMatrixV4WitnessAnchorVerificationPolicy
            or not isinstance(config.controller_private_key, Ed25519PrivateKey)
            or type(config.request_lifetime_seconds) is not int
            or config.request_lifetime_seconds <= 0
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CONFIG_INVALID")
        if not callable(getattr(config.transport, "read_signed_head", None)) or not callable(
            getattr(config.transport, "append_signed_request", None)
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CONFIG_INVALID")
        if not callable(getattr(config.clock, "now_utc", None)) or not callable(
            getattr(config.replay_id_source, "next_controller_append_replay_id", None)
        ) or not callable(
            getattr(config.read_challenge_source, "next_witness_read_challenge", None)
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CONFIG_INVALID")

        # The controller signer must match the policy before any transport
        # operation.  This exposes only its local public projection.
        try:
            controller_public_key = (
                config.controller_private_key.public_key().public_bytes(
                    Encoding.Raw,
                    PublicFormat.Raw,
                )
            )
        except (TypeError, ValueError) as exc:
            raise PhysicalFullMatrixV4WitnessAnchorAdapterError(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CONFIG_INVALID"
            ) from exc
        if controller_public_key != config.policy.controller_public_key:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CONTROLLER_SIGNER_MISMATCH")

        try:
            canonical_genesis = (
                _wire.canonical_physical_full_matrix_v4_witness_anchor_genesis_bytes(
                    config.policy.genesis
                )
            )
        except _wire.PhysicalFullMatrixV4WitnessAnchorWireError as exc:
            raise PhysicalFullMatrixV4WitnessAnchorAdapterError(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CONFIG_INVALID"
            ) from exc

        self._config = config
        self._lock = threading.RLock()
        self._clock_floor: datetime | None = None
        now = self._now_locked()
        try:
            genesis = _wire.verified_physical_full_matrix_v4_witness_anchor_genesis_head(
                policy=config.policy,
                now=now,
            )
        except _wire.PhysicalFullMatrixV4WitnessAnchorWireError as exc:
            raise PhysicalFullMatrixV4WitnessAnchorAdapterError(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CONFIG_INVALID"
            ) from exc
        if config.request_lifetime_seconds > config.policy.maximum_request_lifetime_seconds:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CONFIG_INVALID")

        self._identity = PhysicalFullMatrixV4WitnessAnchorPolicyIdentity(
            schema=PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_IDENTITY_SCHEMA,
            journal_binding_sha256=genesis.journal_binding_sha256,
            baseline_plan_binding_sha256=genesis.baseline_plan_binding_sha256,
            run_id=genesis.run_id,
            plan_sha256=genesis.plan_sha256,
            anchor_genesis_sequence=genesis.sequence,
            anchor_genesis_head_sha256=genesis.head_sha256,
            canonical_genesis_sha256=hashlib.sha256(canonical_genesis).hexdigest(),
        )
        self._canonical_genesis = canonical_genesis
        self._current = _ObservedAnchor(genesis, canonical_genesis)
        self._has_observed_remote_head = False
        self._seen_replay_ids: set[str] = set()
        self._seen_read_challenges: set[str] = set()
        self._seen_observation_ids: set[str] = set()

    @property
    def policy_identity(self) -> PhysicalFullMatrixV4WitnessAnchorPolicyIdentity:
        """Return only the non-secret fixed scope required by transport."""

        return self._identity

    def __reduce_ex__(self, protocol: int):
        del protocol
        raise TypeError("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_NON_SERIALIZABLE")

    def _now_locked(self) -> datetime:
        try:
            value = self._config.clock.now_utc()
        except PhysicalFullMatrixV4WitnessAnchorAdapterError:
            raise
        except Exception as exc:
            raise PhysicalFullMatrixV4WitnessAnchorAdapterError(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CLOCK_INVALID"
            ) from exc
        now = _as_utc(
            value,
            code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CLOCK_INVALID",
        )
        if self._clock_floor is not None and now < self._clock_floor:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_CLOCK_ROLLBACK")
        self._clock_floor = now
        return now

    def _new_read_challenge_locked(self) -> str:
        try:
            value = self._config.read_challenge_source.next_witness_read_challenge(
                policy_identity=self._identity,
            )
        except PhysicalFullMatrixV4WitnessAnchorAdapterError:
            raise
        except Exception as exc:
            raise PhysicalFullMatrixV4WitnessAnchorAdapterError(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_READ_CHALLENGE_FAILED"
            ) from exc
        if (
            type(value) is not str
            or _SHA256_RE.fullmatch(value) is None
            or value in self._seen_read_challenges
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_READ_CHALLENGE_INVALID")
        # Burn before external dispatch.  An ambiguous response must never
        # cause a controller challenge to be reused.
        self._seen_read_challenges.add(value)
        return value

    def _new_replay_id_locked(self) -> str:
        try:
            value = self._config.replay_id_source.next_controller_append_replay_id(
                policy_identity=self._identity,
            )
        except PhysicalFullMatrixV4WitnessAnchorAdapterError:
            raise
        except Exception as exc:
            raise PhysicalFullMatrixV4WitnessAnchorAdapterError(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_REPLAY_ID_FAILED"
            ) from exc
        if (
            type(value) is not str
            or _SHA256_RE.fullmatch(value) is None
            or value in self._seen_replay_ids
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_REPLAY_ID_INVALID")
        return value

    def _read_wire_bytes_locked(self, *, read_challenge: str) -> bytes:
        try:
            raw = self._config.transport.read_signed_head(
                policy_identity=self._identity,
                read_challenge=read_challenge,
            )
        except PhysicalFullMatrixV4WitnessAnchorAdapterError:
            raise
        except Exception as exc:
            raise PhysicalFullMatrixV4WitnessAnchorAdapterError(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_TRANSPORT_READ_FAILED"
            ) from exc
        if type(raw) is not bytes:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_TRANSPORT_READ_INVALID")
        return raw

    def _append_wire_bytes_locked(self, *, request: bytes, read_challenge: str) -> bytes:
        try:
            raw = self._config.transport.append_signed_request(
                policy_identity=self._identity,
                canonical_controller_append_request=request,
                read_challenge=read_challenge,
            )
        except PhysicalFullMatrixV4WitnessAnchorAdapterError:
            raise
        except Exception as exc:
            raise PhysicalFullMatrixV4WitnessAnchorAdapterError(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_TRANSPORT_APPEND_FAILED"
            ) from exc
        if type(raw) is not bytes:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_TRANSPORT_APPEND_INVALID")
        return raw

    def _canonical_anchor_locked(self, value: _VerifiedAnchor) -> bytes:
        if type(value) is _wire.VerifiedPhysicalFullMatrixV4WitnessAnchorHead:
            canonical = value.canonical_head
        elif type(value) is _wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead:
            canonical = value.canonical_immutable_head
        else:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_WIRE_HEAD_INVALID")
        if type(canonical) is not bytes:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_WIRE_HEAD_INVALID")
        return canonical

    def _is_configured_genesis_locked(self, value: _VerifiedAnchor) -> bool:
        return type(value) is _wire.VerifiedPhysicalFullMatrixV4WitnessAnchorHead

    def _validate_expected_tail_locked(
        self,
        *,
        sequence: object,
        head_sha256: object,
    ) -> tuple[int, str]:
        if type(sequence) is not int or sequence < self._identity.anchor_genesis_sequence:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_EXPECTED_TAIL_INVALID")
        if type(head_sha256) is not str or _SHA256_RE.fullmatch(head_sha256) is None:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_EXPECTED_TAIL_INVALID")
        return sequence, head_sha256

    def _observe_verified_anchor_locked(
        self,
        *,
        anchor: _VerifiedAnchor,
        expected_sequence: int,
        expected_head_sha256: str,
        require_successor: bool,
    ) -> _VerifiedAnchor:
        canonical = self._canonical_anchor_locked(anchor)
        exact_current = (
            anchor.sequence == expected_sequence
            and anchor.head_sha256 == expected_head_sha256
        )
        immediate_successor = (
            anchor.sequence == expected_sequence + 1
            and getattr(anchor, "previous_head_sha256", None) == expected_head_sha256
        )
        if not exact_current and not immediate_successor:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_EXPECTED_TAIL_MISMATCH")
        if require_successor and not immediate_successor:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_PREDECESSOR_MISMATCH")

        if self._is_configured_genesis_locked(anchor):
            if (
                not exact_current
                or anchor.sequence != self._identity.anchor_genesis_sequence
                or anchor.head_sha256 != self._identity.anchor_genesis_head_sha256
                or canonical != self._canonical_genesis
            ):
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_GENESIS_MISMATCH")
        elif (
            type(anchor) is not _wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead
            or anchor.sequence <= self._identity.anchor_genesis_sequence
            or anchor.previous_head_sha256 == _ZERO_SHA256
            or anchor.commitment_sha256 == _ZERO_SHA256
            or anchor.controller_request_sha256 == _ZERO_SHA256
            or anchor.immutable_attestation_sha256 == _ZERO_SHA256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_WIRE_HEAD_INVALID")

        # A process-local cache is an additional monotonicity check only.  A
        # new adapter may begin at a later journal-pinned tail, so it cannot
        # replace the Journal's durable expected-tail argument above.
        current = self._current.verified
        if self._has_observed_remote_head:
            if anchor.sequence < current.sequence:
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_HEAD_STALE_OR_DIVERGENT")
            if anchor.sequence == current.sequence:
                if (
                    anchor.head_sha256 != current.head_sha256
                    or canonical != self._current.canonical_anchor
                ):
                    _fail(
                        "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_HEAD_STALE_OR_DIVERGENT"
                    )
            elif (
                anchor.sequence != current.sequence + 1
                or getattr(anchor, "previous_head_sha256", None) != current.head_sha256
            ):
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_HEAD_STALE_OR_DIVERGENT")
        self._current = _ObservedAnchor(anchor, canonical)
        self._has_observed_remote_head = True
        return anchor

    def _verify_envelope_locked(
        self,
        *,
        raw: bytes,
        read_challenge: str,
        expected_sequence: int | None = None,
        expected_head_sha256: str | None = None,
        expected_predecessor: _VerifiedAnchor | None = None,
        append_request: _wire.VerifiedPhysicalFullMatrixV4WitnessAnchorAppendRequest | None = None,
        code: str,
    ) -> _wire.VerifiedPhysicalFullMatrixV4WitnessAnchorTransportEnvelope:
        verified = _wire_error(
            code,
            lambda: _wire.verify_physical_full_matrix_v4_witness_anchor_transport_envelope(
                raw,
                policy=self._config.policy,
                now=self._now_locked(),
                expected_read_challenge=read_challenge,
                expected_predecessor=expected_predecessor,
                append_request=append_request,
                expected_current_sequence=expected_sequence,
                expected_current_head_sha256=expected_head_sha256,
                seen_observation_ids=frozenset(self._seen_observation_ids),
            ),
        )
        observation = verified.read_observation
        if (
            verified.read_challenge != read_challenge
            or observation.read_challenge != read_challenge
            or observation.observation_id in self._seen_observation_ids
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_OBSERVATION_REPLAYED")
        self._seen_observation_ids.add(observation.observation_id)
        return verified

    def _map_wire_commitment_locked(
        self,
        value: object,
    ) -> _wire.PhysicalFullMatrixV4WitnessAnchorCommitment:
        if type(value) is not _journal.PhysicalFullMatrixV4WitnessJournalAnchorCommitment:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_COMMITMENT_INVALID")
        commitment = value
        if (
            commitment.schema
            != _journal.PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_COMMITMENT_SCHEMA
            or commitment.journal_binding_sha256 != self._identity.journal_binding_sha256
            or commitment.baseline_plan_binding_sha256
            != self._identity.baseline_plan_binding_sha256
            or commitment.run_id != self._identity.run_id
            or commitment.plan_sha256 != self._identity.plan_sha256
            or commitment.anchor_genesis_sequence
            != self._identity.anchor_genesis_sequence
            or commitment.anchor_genesis_head_sha256
            != self._identity.anchor_genesis_head_sha256
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_COMMITMENT_INVALID")
        return _wire_error(
            "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_COMMITMENT_INVALID",
            lambda: _wire.build_physical_full_matrix_v4_witness_anchor_commitment(
                journal_binding_sha256=commitment.journal_binding_sha256,
                baseline_plan_binding_sha256=commitment.baseline_plan_binding_sha256,
                run_id=commitment.run_id,
                plan_sha256=commitment.plan_sha256,
                anchor_genesis_sequence=commitment.anchor_genesis_sequence,
                anchor_genesis_head_sha256=commitment.anchor_genesis_head_sha256,
                event=commitment.event,
                phase_sequence=commitment.phase_sequence,
                phase_request_sha256=commitment.phase_request_sha256,
                effect_key=commitment.effect_key,
                claim_id=commitment.claim_id,
                receipt_sha256=commitment.receipt_sha256,
                previous_anchor_sequence=commitment.previous_anchor_sequence,
                previous_anchor_head_sha256=commitment.previous_anchor_head_sha256,
                local_previous_record_sha256=commitment.local_previous_record_sha256,
                local_event_sha256=commitment.local_event_sha256,
                occurred_at=commitment.occurred_at,
            ),
        )

    def _journal_commitment_locked(
        self,
        value: _wire.PhysicalFullMatrixV4WitnessAnchorCommitment,
    ) -> _journal.PhysicalFullMatrixV4WitnessJournalAnchorCommitment:
        return _journal.PhysicalFullMatrixV4WitnessJournalAnchorCommitment(
            schema=_journal.PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_COMMITMENT_SCHEMA,
            journal_binding_sha256=value.journal_binding_sha256,
            baseline_plan_binding_sha256=value.baseline_plan_binding_sha256,
            anchor_genesis_sequence=value.anchor_genesis_sequence,
            anchor_genesis_head_sha256=value.anchor_genesis_head_sha256,
            event=value.event,
            run_id=value.run_id,
            plan_sha256=value.plan_sha256,
            phase_sequence=value.phase_sequence,
            phase_request_sha256=value.phase_request_sha256,
            effect_key=value.effect_key,
            claim_id=value.claim_id,
            previous_anchor_sequence=value.previous_anchor_sequence,
            previous_anchor_head_sha256=value.previous_anchor_head_sha256,
            local_previous_record_sha256=value.local_previous_record_sha256,
            local_event_sha256=value.local_event_sha256,
            receipt_sha256=value.receipt_sha256,
            occurred_at=value.occurred_at,
        )

    def _journal_head_locked(
        self,
        value: _VerifiedAnchor,
    ) -> _journal.PhysicalFullMatrixV4WitnessJournalAnchorHead:
        if self._is_configured_genesis_locked(value):
            if (
                value.head_sha256 != self._identity.anchor_genesis_head_sha256
                or self._canonical_anchor_locked(value) != self._canonical_genesis
            ):
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_GENESIS_MISMATCH")
            # The zeros are solely the journal's representation sentinel for
            # its configured signed genesis; no external hash is projected.
            return _journal.PhysicalFullMatrixV4WitnessJournalAnchorHead(
                schema=_journal.PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_ANCHOR_SCHEMA,
                journal_binding_sha256=value.journal_binding_sha256,
                baseline_plan_binding_sha256=value.baseline_plan_binding_sha256,
                sequence=value.sequence,
                head_sha256=value.head_sha256,
                previous_head_sha256=_ZERO_SHA256,
                commitment_sha256=_ZERO_SHA256,
                attestation_sha256=_ZERO_SHA256,
                commitment=None,
            )
        if type(value) is not _wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead:
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_WIRE_HEAD_INVALID")
        if (
            value.commitment_sha256
            != _wire_error(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_WIRE_HEAD_INVALID",
                lambda: _wire.derive_physical_full_matrix_v4_witness_anchor_commitment_sha256(
                    value.commitment
                ),
            )
        ):
            _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_WIRE_HEAD_INVALID")
        return _journal.PhysicalFullMatrixV4WitnessJournalAnchorHead(
            schema=_journal.PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_ANCHOR_SCHEMA,
            journal_binding_sha256=value.journal_binding_sha256,
            baseline_plan_binding_sha256=value.baseline_plan_binding_sha256,
            sequence=value.sequence,
            head_sha256=value.head_sha256,
            previous_head_sha256=value.previous_head_sha256,
            commitment_sha256=value.commitment_sha256,
            attestation_sha256=value.immutable_attestation_sha256,
            commitment=self._journal_commitment_locked(value.commitment),
        )

    def read_head(
        self,
        *,
        journal_binding_sha256: str,
        baseline_plan_binding_sha256: str,
        expected_anchor_sequence: int,
        expected_anchor_head_sha256: str,
    ) -> _journal.PhysicalFullMatrixV4WitnessJournalAnchorHead:
        """Require one fresh challenge-bound observation before journal mapping."""

        with self._lock:
            if (
                journal_binding_sha256 != self._identity.journal_binding_sha256
                or baseline_plan_binding_sha256
                != self._identity.baseline_plan_binding_sha256
            ):
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_BINDING_MISMATCH")
            expected_sequence, expected_head = self._validate_expected_tail_locked(
                sequence=expected_anchor_sequence,
                head_sha256=expected_anchor_head_sha256,
            )
            challenge = self._new_read_challenge_locked()
            envelope = self._verify_envelope_locked(
                raw=self._read_wire_bytes_locked(read_challenge=challenge),
                read_challenge=challenge,
                expected_sequence=expected_sequence,
                expected_head_sha256=expected_head,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_READ_ENVELOPE_INVALID",
            )
            anchor = self._observe_verified_anchor_locked(
                anchor=envelope.anchor_head,
                expected_sequence=expected_sequence,
                expected_head_sha256=expected_head,
                require_successor=False,
            )
            return self._journal_head_locked(anchor)

    def append_commitment(
        self,
        *,
        commitment: _journal.PhysicalFullMatrixV4WitnessJournalAnchorCommitment,
    ) -> _journal.PhysicalFullMatrixV4WitnessJournalAnchorReceipt:
        """Append one exact Wire commitment and require a fresh signed response."""

        with self._lock:
            if not self._has_observed_remote_head:
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_FRESH_HEAD_REQUIRED")
            wire_commitment = self._map_wire_commitment_locked(commitment)
            predecessor = self._current.verified
            if (
                wire_commitment.previous_anchor_sequence != predecessor.sequence
                or wire_commitment.previous_anchor_head_sha256 != predecessor.head_sha256
            ):
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_PREDECESSOR_MISMATCH")

            replay_id = self._new_replay_id_locked()
            issued_at = self._now_locked()
            expires_at = issued_at + timedelta(
                seconds=self._config.request_lifetime_seconds
            )
            raw_request = _wire_error(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_REQUEST_INVALID",
                lambda: _wire.build_physical_full_matrix_v4_witness_anchor_controller_append_request(
                    policy=self._config.policy,
                    predecessor=predecessor,
                    commitment=wire_commitment,
                    replay_id=replay_id,
                    issued_at=issued_at,
                    expires_at=expires_at,
                    controller_private_key=self._config.controller_private_key,
                ),
            )
            verified_request = _wire_error(
                "PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_REQUEST_INVALID",
                lambda: _wire.verify_physical_full_matrix_v4_witness_anchor_controller_append_request(
                    raw_request,
                    policy=self._config.policy,
                    predecessor=predecessor,
                    now=self._now_locked(),
                    seen_replay_ids=frozenset(self._seen_replay_ids),
                ),
            )
            self._seen_replay_ids.add(replay_id)
            challenge = self._new_read_challenge_locked()
            envelope = self._verify_envelope_locked(
                raw=self._append_wire_bytes_locked(
                    request=raw_request,
                    read_challenge=challenge,
                ),
                read_challenge=challenge,
                expected_predecessor=predecessor,
                append_request=verified_request,
                code="PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_APPEND_RESULT_INVALID",
            )
            anchor = self._observe_verified_anchor_locked(
                anchor=envelope.anchor_head,
                expected_sequence=predecessor.sequence,
                expected_head_sha256=predecessor.head_sha256,
                require_successor=True,
            )
            if type(anchor) is not _wire.VerifiedPhysicalFullMatrixV4WitnessAnchorImmutableHead:
                _fail("PHYSICAL_FULL_MATRIX_V4_WITNESS_ANCHOR_ADAPTER_APPEND_RESULT_INVALID")
            return _journal.PhysicalFullMatrixV4WitnessJournalAnchorReceipt(
                schema=_journal.PHYSICAL_FULL_MATRIX_V4_WITNESS_JOURNAL_ANCHOR_SCHEMA,
                journal_binding_sha256=anchor.journal_binding_sha256,
                baseline_plan_binding_sha256=anchor.baseline_plan_binding_sha256,
                sequence=anchor.sequence,
                previous_head_sha256=anchor.previous_head_sha256,
                head_sha256=anchor.head_sha256,
                commitment_sha256=anchor.commitment_sha256,
                attestation_sha256=anchor.immutable_attestation_sha256,
            )
