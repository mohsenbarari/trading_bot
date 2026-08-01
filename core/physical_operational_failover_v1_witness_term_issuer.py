"""Root-local Witness issuer for V1 current-term attestations.

This is the producer-side companion to
``physical_operational_failover_v1_witness_term_revalidator``.  It is a
small, default-off, Witness-only boundary: it obtains exactly one already
durable ledger snapshot through an injected *local read* seam, validates that
the requested holder is the currently active holder, and signs the existing
canonical current-term-attestation grammar.

It deliberately has no transport, provider, database, Object Storage,
writer-start, traffic, promotion, or ledger-transition capability.  In
particular, it never receives an append/CAS interface and never changes a
ledger state or term.  A later root-owned service must install an authenticated
role-local transport and a hardware- or root-key-backed signing boundary.  The
result here is only signed evidence; it carries no authority flag.

The attestation grammar itself pins the exact recipient configuration,
revalidation request, durable reservation, ledger version/head/entry/state,
phase, and active term.  This issuer additionally pins its current-term signer
to a key that is distinct from every V1 evidence signer, including the Witness
promotion-grant signer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import os
import secrets
from typing import Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_operational_failover_v1 as evidence
from core import physical_operational_failover_v1_witness_ledger as ledger
from core import physical_operational_failover_v1_witness_term_revalidator as revalidator
from core import physical_operational_failover_v1_writer_admission as admission


__all__ = (
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_DEFAULT_ENABLED",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_SCHEMA",
    "PhysicalOperationalFailoverV1WitnessCurrentTermIssuer",
    "PhysicalOperationalFailoverV1WitnessCurrentTermIssuerClock",
    "PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError",
    "PhysicalOperationalFailoverV1WitnessCurrentTermSnapshotReader",
    "RootOwnedPhysicalOperationalFailoverV1WitnessCurrentTermIssuerConfig",
)


PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-witness-current-term-issuer-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_DEFAULT_ENABLED = False


class PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError(
    revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError
):
    """A Witness-only current-term attestation could not be issued safely."""


def _fail(code: str) -> None:
    raise PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError(code)


class PhysicalOperationalFailoverV1WitnessCurrentTermIssuerClock(Protocol):
    """Trusted local Witness time; callers cannot provide issuance time."""

    def now_utc(self) -> datetime: ...


class PhysicalOperationalFailoverV1WitnessCurrentTermSnapshotReader(Protocol):
    """Read-only, local seam for one exact durable Witness-ledger snapshot.

    The narrow name is intentional: a future runtime can adapt a root-owned
    durable CAS store, but this issuer is never handed a generic store or any
    mutation method.
    """

    def read_current_witness_ledger_snapshot(
        self,
    ) -> ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot | None: ...


@dataclass(frozen=True)
class RootOwnedPhysicalOperationalFailoverV1WitnessCurrentTermIssuerConfig:
    """Fixed-Witness, default-off policy with no caller-selected path or site."""

    schema: str = PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_SCHEMA
    enabled: bool = PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_DEFAULT_ENABLED
    revalidator_config: (
        revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig | None
    ) = None
    ledger_config: ledger.RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig | None = None


@dataclass(frozen=True)
class _Facts:
    revalidator_config: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig
    revalidator_facts: object
    ledger_config: ledger.RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig
    binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding
    signer_public_key_raw: bytes
    safety_margin_seconds: int
    maximum_attestation_duration_seconds: int


def _require_root_runtime() -> None:
    try:
        if os.geteuid() != 0:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_ROOT_RUNTIME_REQUIRED")
    except OSError as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_ROOT_RUNTIME_REQUIRED"
        ) from exc


def _revalidator_config_facts(value: object) -> object:
    try:
        return revalidator._config(value)
    except revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_REVALIDATOR_CONFIG_INVALID"
        ) from exc


def _ledger_config_facts(value: object) -> object:
    try:
        return ledger._facts(value)
    except ledger.PhysicalOperationalFailoverV1WitnessLedgerError as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_LEDGER_CONFIG_INVALID"
        ) from exc


def _config(value: object) -> _Facts:
    if type(value) is not RootOwnedPhysicalOperationalFailoverV1WitnessCurrentTermIssuerConfig:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_CONFIG_INVALID")
    assert isinstance(value, RootOwnedPhysicalOperationalFailoverV1WitnessCurrentTermIssuerConfig)
    if (
        value.schema != PHYSICAL_OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_SCHEMA
        or value.enabled is not True
        or type(value.revalidator_config)
        is not revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig
        or type(value.ledger_config) is not ledger.RootOwnedPhysicalOperationalFailoverV1WitnessLedgerConfig
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_CONFIG_INVALID")

    revalidator_facts = _revalidator_config_facts(value.revalidator_config)
    ledger_facts = _ledger_config_facts(value.ledger_config)
    binding = revalidator_facts.binding
    verification = ledger_facts.verification_config
    pins = verification.pins
    if (
        value.ledger_config.schema != revalidator_facts.expected_ledger_schema
        or pins is None
        or pins.cluster_id != binding.cluster_id
        or pins.release_sha != binding.release_sha
        or pins.stream_generation_id != binding.generation_id
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_CONFIG_BINDING_MISMATCH")

    # ``witness_term_signer_public_key`` is the original V1 promotion-grant
    # role.  The revalidator grammar separately pins its own current-term
    # attestation key.  Require both the intended relationship and strict
    # non-reuse across every V1 evidence role.
    signer_public_key_raw = revalidator_facts.attestation_public_key_raw
    if (
        revalidator_facts.promotion_public_key_raw
        != verification.witness_term_signer_public_key
        or signer_public_key_raw
        in {
            verification.fi_self_fence_signer_public_key,
            verification.ir_promotion_request_signer_public_key,
            verification.witness_term_signer_public_key,
            verification.ir_promotion_completion_signer_public_key,
        }
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_KEY_ROLE_COLLISION")
    return _Facts(
        revalidator_config=value.revalidator_config,
        revalidator_facts=revalidator_facts,
        ledger_config=value.ledger_config,
        binding=binding,
        signer_public_key_raw=signer_public_key_raw,
        safety_margin_seconds=revalidator_facts.safety_margin_seconds,
        maximum_attestation_duration_seconds=revalidator_facts.maximum_attestation_duration_seconds,
    )


def _private_key(value: object, *, facts: _Facts) -> Ed25519PrivateKey:
    if not isinstance(value, Ed25519PrivateKey):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_PRIVATE_KEY_INVALID")
    try:
        public = value.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    except (TypeError, ValueError) as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_PRIVATE_KEY_INVALID"
        ) from exc
    if public != facts.signer_public_key_raw:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_PRIVATE_KEY_ROLE_MISMATCH")
    return value


def _trusted_now(
    clock: object,
    *,
    floor: datetime | None,
) -> datetime:
    try:
        return revalidator._trusted_now(clock, floor=floor)
    except revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_CLOCK_INVALID"
        ) from exc


def _request_facts(value: object, *, facts: _Facts) -> object:
    try:
        return revalidator._request_facts(value, facts=facts.revalidator_facts)
    except revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_REQUEST_INVALID"
        ) from exc


def _reservation(
    value: object,
    *,
    facts: _Facts,
    request_facts: object,
    now: datetime,
) -> revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation:
    if type(value) is not revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_RESERVATION_INVALID")
    try:
        requested_at = revalidator._utc(
            value.requested_at,
            code="OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_RESERVATION_INVALID",
        )
        expected = revalidator._reservation_request(
            facts=facts.revalidator_facts,
            request=request_facts,
            now=requested_at,
        )
        return revalidator._reservation(
            value,
            request=expected,
            facts=facts.revalidator_facts,
            now=now,
        )
    except revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_RESERVATION_MISMATCH"
        ) from exc


def _read_exact_snapshot(
    reader: object,
) -> ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
    callback = getattr(reader, "read_current_witness_ledger_snapshot", None)
    if not callable(callback):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_SNAPSHOT_READER_MISSING")
    try:
        snapshot = callback()
    except PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_SNAPSHOT_READ_FAILED"
        ) from exc
    if snapshot is None:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_LEDGER_NOT_BOOTSTRAPPED")
    if type(snapshot) is not ledger.PhysicalOperationalFailoverV1WitnessLedgerSnapshot:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_SNAPSHOT_INVALID")
    return snapshot


def _snapshot_facts(
    value: object,
    *,
    now: datetime,
) -> object:
    try:
        return revalidator._snapshot(value, now=now)
    except revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError as exc:
        raise PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError(
            "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_LEDGER_NOT_CURRENT"
        ) from exc


def _reservation_head_is_current(
    *,
    reservation: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
    snapshot_facts: object,
) -> None:
    version = snapshot_facts.snapshot.version
    head = snapshot_facts.snapshot.head_sha256
    if version < reservation.minimum_ledger_version:
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_LEDGER_SNAPSHOT_STALE")
    if (
        version == reservation.minimum_ledger_version
        and reservation.previous_ledger_head_sha256 is not None
        and head != reservation.previous_ledger_head_sha256
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_LEDGER_SNAPSHOT_ROLLBACK")


def _attestation_expiry(
    *,
    now: datetime,
    reservation: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
    snapshot_facts: object,
    facts: _Facts,
) -> datetime:
    term = snapshot_facts.active_term
    if term.issued_at > now or term.expires_at <= now + timedelta(
        seconds=facts.safety_margin_seconds
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_TERM_NOT_CURRENT")
    expires_at = min(
        term.expires_at,
        reservation.expires_at,
        now + timedelta(seconds=facts.maximum_attestation_duration_seconds),
    )
    if expires_at <= now + timedelta(seconds=facts.safety_margin_seconds):
        _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_ATTESTATION_WINDOW_UNSAFE")
    return expires_at


def _attestation_id() -> str:
    return "witness-current-term-attestation-" + secrets.token_urlsafe(24)


def _attestation_nonce() -> str:
    return secrets.token_urlsafe(24)


class PhysicalOperationalFailoverV1WitnessCurrentTermIssuer:
    """Root-only issuer for one signed projection of one local active term.

    ``issue_current_term_attestation`` deliberately reads the ledger exactly
    once and returns that exact snapshot beside the canonical signed bytes.
    It does not perform a second read, a write, a promotion, or a writer
    operation.  The recipient's durable guard remains responsible for durable
    reservation and consumption semantics.
    """

    def __init__(
        self,
        *,
        config: RootOwnedPhysicalOperationalFailoverV1WitnessCurrentTermIssuerConfig,
        snapshot_reader: PhysicalOperationalFailoverV1WitnessCurrentTermSnapshotReader,
        clock: PhysicalOperationalFailoverV1WitnessCurrentTermIssuerClock,
        current_term_private_key: Ed25519PrivateKey,
    ) -> None:
        _require_root_runtime()
        self._facts = _config(config)
        if not callable(getattr(snapshot_reader, "read_current_witness_ledger_snapshot", None)):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_SNAPSHOT_READER_MISSING")
        if not callable(getattr(clock, "now_utc", None)):
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_CLOCK_MISSING")
        self._private_key = _private_key(current_term_private_key, facts=self._facts)
        self._snapshot_reader = snapshot_reader
        self._clock = clock

    def issue_current_term_attestation(
        self,
        *,
        request: admission.PhysicalOperationalFailoverV1WriterTermRevalidationRequest,
        reservation: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidationReservation,
    ) -> revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationFetchResponse:
        """Issue no evidence unless one exact snapshot is active and fresh."""

        _require_root_runtime()
        request_facts = _request_facts(request, facts=self._facts)
        now = _trusted_now(self._clock, floor=request_facts.clock_floor)
        normalized_reservation = _reservation(
            reservation,
            facts=self._facts,
            request_facts=request_facts,
            now=now,
        )

        # This is intentionally the only snapshot-reader call in this method.
        snapshot = _read_exact_snapshot(self._snapshot_reader)
        snapshot_facts = _snapshot_facts(snapshot, now=now)
        _reservation_head_is_current(
            reservation=normalized_reservation,
            snapshot_facts=snapshot_facts,
        )
        if snapshot_facts.active_term.holder_site != request_facts.binding.local_site:
            _fail("OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_ACTIVE_HOLDER_MISMATCH")
        expires_at = _attestation_expiry(
            now=now,
            reservation=normalized_reservation,
            snapshot_facts=snapshot_facts,
            facts=self._facts,
        )

        value = revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationInput(
            attestation_id=_attestation_id(),
            attestation_nonce=_attestation_nonce(),
            issued_at=now,
            expires_at=expires_at,
            cluster_id=request_facts.binding.cluster_id,
            holder_site=snapshot_facts.active_term.holder_site,
            release_sha=request_facts.binding.release_sha,
            generation_id=request_facts.binding.generation_id,
            runtime_instance_id=request_facts.runtime_instance_id,
            revalidation_id=request_facts.revalidation_id,
            reservation_id=normalized_reservation.reservation_id,
            request_sha256=request_facts.request_sha256,
            ledger_schema=self._facts.revalidator_facts.expected_ledger_schema,
            ledger_version=snapshot_facts.snapshot.version,
            ledger_head_sha256=snapshot_facts.snapshot.head_sha256,
            ledger_entry_sha256=snapshot_facts.snapshot.entry.entry_sha256,
            ledger_previous_head_sha256=snapshot_facts.snapshot.entry.previous_head_sha256,
            ledger_state_sha256=snapshot_facts.snapshot.entry.state_sha256,
            ledger_phase=snapshot_facts.phase,
            active_term=snapshot_facts.active_term,
            active_term_sha256=snapshot_facts.active_term_sha256,
        )
        try:
            raw = revalidator.sign_physical_operational_failover_v1_witness_current_term_attestation(
                value=value,
                config=self._facts.revalidator_config,
                private_key=self._private_key,
            )
            # The public verifier is deliberately used before returning so
            # producer and consumer share one exact grammar/binding check.
            revalidator.verify_physical_operational_failover_v1_witness_current_term_attestation(
                value=raw,
                config=self._facts.revalidator_config,
                request=request,
                reservation=normalized_reservation,
                ledger_snapshot=snapshot,
                now=now,
            )
        except revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError as exc:
            raise PhysicalOperationalFailoverV1WitnessCurrentTermIssuerError(
                "OPERATIONAL_FAILOVER_V1_WITNESS_TERM_ISSUER_ATTESTATION_INVALID"
            ) from exc
        return revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAttestationFetchResponse(
            canonical_attestation=raw,
            ledger_snapshot=snapshot,
        )
