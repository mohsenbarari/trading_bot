"""Runtime-only, two-stage composition for the V1/V2 writer-term bridge.

The low-level bridge contract deliberately accepts public scalar dataclasses
so it can verify a canonical certificate independently.  Those dataclasses
are not an acceptable runtime input boundary: they could be reconstructed by
a caller and they do not prove an exact current V1 admission, V1 Witness
provenance, V2 prepare, or SQL parent receipt.

This module owns that missing composition boundary without doing I/O:

* ``issue_pre_transaction`` accepts only three opaque capabilities, consumes
  the one-shot V1 provenance with the exact V1 admission, projects the exact
  V2 prepare after fresh validation, then signs **and immediately verifies**
  a short pre-transaction certificate;
* an issued capability can release only the exact opaque V1 admission and V2
  prepare that created it to the root-owned local transaction; and
* ``bind_post_flush`` accepts only that issued capability and an opaque V1
  SQL receipt.  It consumes the receipt, internally constructs the raw
  bridge parent projection, and binds the already verified certificate.

The second stage intentionally happens only after the caller has flushed the
V1 parent in its local transaction.  A certificate never claims that a parent
row exists before that flush.  No function in this module opens a database,
performs network/filesystem work, starts a worker, changes traffic, or makes
a promotion decision.  Everything is default-off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Protocol
from weakref import WeakKeyDictionary

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import physical_operational_failover_v1_writer_admission as admission
from core import physical_operational_failover_v1_writer_admission_sqlalchemy_transaction as sql
from core import physical_operational_failover_v1_v2_writer_term_bridge as bridge
from core import physical_operational_failover_v1_witness_term_revalidator as revalidator
from core import physical_wal_v2_witness_roundtrip_strict_writer_response as v2


__all__ = (
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_DEFAULT_ENABLED",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_SCHEMA",
    "IssuedPhysicalOperationalFailoverV1V2WriterTermBridgeCertificate",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuer",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerClock",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerConfig",
    "PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError",
)


PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_SCHEMA = (
    "gold-trade-physical-operational-failover-v1-v2-writer-term-bridge-runtime-issuer-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_DEFAULT_ENABLED = (
    False
)

_ISSUED_CAPABILITY = object()


class PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(ValueError):
    """The opaque V1/V2-to-bridge composition is unsafe or inconsistent."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(code)


class PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerClock(Protocol):
    """Trusted root-owned certificate clock, at canonical second precision."""

    def now_utc(self) -> datetime: ...


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerConfig:
    """Pinned enabled policies for one local two-stage issuer."""

    enabled: bool = PHYSICAL_OPERATIONAL_FAILOVER_V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_DEFAULT_ENABLED
    bridge_config: bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeConfig | None = None
    v1_revalidator_config: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig | None = None
    v1_sqlalchemy_transaction_config: sql.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig | None = None
    v2_strict_writer_config: v2.PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig | None = None


@dataclass(frozen=True, eq=False, init=False)
class IssuedPhysicalOperationalFailoverV1V2WriterTermBridgeCertificate:
    """Opaque verified pre-transaction certificate capability.

    The public fields are audit identifiers only.  The canonical certificate,
    exact V1 admission, and exact V2 prepared handle remain in a private
    identity registry until the caller either uses them in its local
    transaction or binds the V1 SQL parent after flush.
    """

    certificate_id: str
    certificate_sha256: str
    intent_sha256: str
    issued_at: datetime
    expires_at: datetime
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        certificate_id: str,
        certificate_sha256: str,
        intent_sha256: str,
        issued_at: datetime,
        expires_at: datetime,
        capability: object,
    ) -> None:
        if capability is not _ISSUED_CAPABILITY:
            raise TypeError(
                "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_CONSTRUCTION_FORBIDDEN"
            )
        object.__setattr__(self, "certificate_id", certificate_id)
        object.__setattr__(self, "certificate_sha256", certificate_sha256)
        object.__setattr__(self, "intent_sha256", intent_sha256)
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_SERIALIZATION_FORBIDDEN"
        )


@dataclass(frozen=True)
class _Facts:
    config: PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerConfig
    bridge_config: bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeConfig
    v1_revalidator_config: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig
    v1_sqlalchemy_transaction_config: sql.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig
    v2_strict_writer_config: v2.PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig
    bridge_facts: object


@dataclass
class _IssuedState:
    facts: _Facts
    canonical_certificate: bytes
    writer_admission: admission.PhysicalOperationalFailoverV1WriterAdmission
    v2_prepared: v2.PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse
    consumed: bool = False


_ISSUED_STATES: WeakKeyDictionary[
    IssuedPhysicalOperationalFailoverV1V2WriterTermBridgeCertificate, _IssuedState
] = WeakKeyDictionary()
_ISSUED_STATES_LOCK = RLock()


def _facts(value: object) -> _Facts:
    if type(value) is not PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerConfig:
        _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_CONFIG_INVALID")
    if value.enabled is not True:
        _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_CONFIG_DISABLED")
    if (
        type(value.bridge_config)
        is not bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeConfig
        or type(value.v1_revalidator_config)
        is not revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorConfig
        or type(value.v1_sqlalchemy_transaction_config)
        is not sql.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig
        or type(value.v2_strict_writer_config)
        is not v2.PhysicalWalV2WitnessRoundtripStrictWriterResponseConfig
    ):
        _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_CONFIG_INVALID")
    try:
        bridge_facts = bridge._facts(value.bridge_config)
        v1_facts = revalidator._config(value.v1_revalidator_config)
        v1_hash = (
            revalidator.physical_operational_failover_v1_witness_current_term_revalidator_configuration_sha256(
                config=value.v1_revalidator_config
            )
        )
        sql_facts = sql._facts(value.v1_sqlalchemy_transaction_config)
        v2_facts = v2._config(value.v2_strict_writer_config)
    except (
        bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeError,
        revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError,
        sql.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError,
        v2.PhysicalWalV2WitnessRoundtripStrictWriterResponseError,
    ) as exc:
        raise PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(
            "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_CONFIG_INVALID"
        ) from exc
    if sql_facts is None:
        _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_CONFIG_INVALID")
    identity = (
        bridge_facts.cluster_id,
        bridge_facts.local_site,
        bridge_facts.release_sha,
        bridge_facts.generation_id,
    )
    if (
        v1_hash != bridge_facts.v1_config_sha256
        or (
            v1_facts.binding.cluster_id,
            v1_facts.binding.local_site,
            v1_facts.binding.release_sha,
            v1_facts.binding.generation_id,
        )
        != identity
        or (
            sql_facts.binding.cluster_id,
            sql_facts.binding.local_site,
            sql_facts.binding.release_sha,
            sql_facts.binding.generation_id,
        )
        != identity
        or v2_facts.configuration_sha256 != bridge_facts.v2_config_sha256
        or v2_facts.source_site != bridge_facts.local_site
    ):
        _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_CONFIG_CROSS_PIN_MISMATCH")
    return _Facts(
        config=value,
        bridge_config=value.bridge_config,
        v1_revalidator_config=value.v1_revalidator_config,
        v1_sqlalchemy_transaction_config=value.v1_sqlalchemy_transaction_config,
        v2_strict_writer_config=value.v2_strict_writer_config,
        bridge_facts=bridge_facts,
    )


def _opaque_inputs(
    *,
    writer_admission: object,
    v1_current_term_provenance: object,
    v2_prepared: object,
) -> tuple[
    admission.PhysicalOperationalFailoverV1WriterAdmission,
    revalidator.BoundPhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenance,
    v2.PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse,
]:
    if (
        type(writer_admission) is not admission.PhysicalOperationalFailoverV1WriterAdmission
        or writer_admission._capability is not admission._ADMISSION_CAPABILITY
        or type(v1_current_term_provenance)
        is not revalidator.BoundPhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenance
        or v1_current_term_provenance._capability is not revalidator._PROVENANCE_CAPABILITY
        or type(v2_prepared)
        is not v2.PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse
        or v2_prepared._capability is not v2._PREPARED_CAPABILITY
    ):
        _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_OPAQUE_CAPABILITY_REQUIRED")
    return writer_admission, v1_current_term_provenance, v2_prepared


def _raw_intent(
    *,
    provenance: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenanceProjection,
    v2_projection: v2.PhysicalWalV2WitnessRoundtripStrictWriterBridgeIntentProjection,
) -> bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeIntent:
    """Create low-level public values only from owner-validated projections."""

    return bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeIntent(
        v1_admission=bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeV1Admission(
            cluster_id=provenance.cluster_id,
            local_site=provenance.local_site,
            release_sha=provenance.release_sha,
            generation_id=provenance.generation_id,
            operation_kind=provenance.operation_kind,
            prior_revision=provenance.prior_revision,
            next_revision=provenance.next_revision,
            fence_generation=provenance.fence_generation,
            evidence_id=provenance.attestation_id,
            revalidation_id=provenance.revalidation_id,
            writer_epoch=provenance.writer_epoch,
            writer_lease_id=provenance.writer_lease_id,
            opened_at=provenance.operation_opened_at,
            admitted_at=provenance.admitted_at,
            term_evidence_issued_at=provenance.attestation_issued_at,
            term_evidence_expires_at=provenance.attestation_expires_at,
        ),
        v1_current_term=bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeCurrentTermProvenance(
            attestation_sha256=provenance.attestation_sha256,
            attestation_id=provenance.attestation_id,
            revalidation_id=provenance.revalidation_id,
            configuration_sha256=provenance.revalidator_configuration_sha256,
            reservation_id=provenance.reservation_id,
            request_sha256=provenance.request_sha256,
            ledger_schema=provenance.ledger_schema,
            ledger_version=provenance.ledger_version,
            ledger_head_sha256=provenance.ledger_head_sha256,
            ledger_entry_sha256=provenance.ledger_entry_sha256,
            ledger_previous_head_sha256=provenance.ledger_previous_head_sha256,
            ledger_state_sha256=provenance.ledger_state_sha256,
            ledger_phase=provenance.ledger_phase,
            active_term_sha256=provenance.active_term_sha256,
            holder_site=provenance.holder_site,
            writer_epoch=provenance.writer_epoch,
            writer_lease_id=provenance.writer_lease_id,
            witness_transition_id=provenance.witness_transition_id,
            witnessed_term_proof_sha256=provenance.witnessed_term_proof_sha256,
            attestation_issued_at=provenance.attestation_issued_at,
            attestation_expires_at=provenance.attestation_expires_at,
            term_issued_at=provenance.term_issued_at,
            term_expires_at=provenance.term_expires_at,
        ),
        v2_instruction=bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeV2Instruction(
            strict_schema=v2_projection.strict_schema,
            configuration_sha256=v2_projection.configuration_sha256,
            atomic_commit_boundary=v2_projection.atomic_commit_boundary,
            commit_id=v2_projection.commit_id,
            attestation_sha256=v2_projection.attestation_sha256,
            context_sha256=v2_projection.context_sha256,
            writer_holder_site=v2_projection.writer_holder_site,
            writer_epoch=v2_projection.writer_epoch,
            writer_lease_id=v2_projection.writer_lease_id,
            witnessed_term_proof_sha256=v2_projection.witnessed_term_proof_sha256,
            witness_transition_id=v2_projection.witness_transition_id,
            activation_mode=v2_projection.activation_mode,
            activation_stream_generation_id=v2_projection.activation_stream_generation_id,
            activation_route_artifact_sha256=v2_projection.activation_route_artifact_sha256,
            activation_source_cutover_attestation_sha256=(
                v2_projection.activation_source_cutover_attestation_sha256
            ),
            activation_receiver_permit_sha256=v2_projection.activation_receiver_permit_sha256,
            attestation_issued_at=v2_projection.attestation_issued_at,
            attestation_expires_at=v2_projection.attestation_expires_at,
            term_issued_at=v2_projection.term_issued_at,
            term_expires_at=v2_projection.term_expires_at,
        ),
    )


def _certificate_expiry(
    *,
    now: datetime,
    facts: _Facts,
    provenance: revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermAdmissionProvenanceProjection,
    v2_projection: v2.PhysicalWalV2WitnessRoundtripStrictWriterBridgeIntentProjection,
) -> datetime:
    """Use the earliest independently verified validity bound, never caller time."""

    return min(
        provenance.attestation_expires_at,
        provenance.term_expires_at,
        v2_projection.attestation_expires_at,
        v2_projection.term_expires_at,
        now + timedelta(seconds=facts.bridge_facts.maximum_certificate_age_seconds),
    )


class PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuer:
    """Owner of the narrow pre-transaction / post-flush bridge sequence."""

    def __init__(
        self,
        *,
        config: PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerConfig,
        bridge_signer_private_key: Ed25519PrivateKey,
        clock: PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerClock,
    ) -> None:
        facts = _facts(config)
        if not callable(getattr(clock, "now_utc", None)):
            _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_CLOCK_MISSING")
        try:
            signer = bridge._private_key(bridge_signer_private_key, facts=facts.bridge_facts)
        except bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeError as exc:
            raise PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(
                "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_SIGNER_INVALID"
            ) from exc
        self._facts = facts
        self._bridge_signer_private_key = signer
        self._clock = clock

    def _now(self) -> datetime:
        try:
            return bridge._utc(
                self._clock.now_utc(),
                code="V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_CLOCK_INVALID",
            )
        except bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeError as exc:
            raise PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(
                "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_CLOCK_INVALID"
            ) from exc

    def _revalidated_issued(
        self,
        value: object,
        *,
        now: datetime,
        permit_consumed: bool = False,
    ) -> tuple[IssuedPhysicalOperationalFailoverV1V2WriterTermBridgeCertificate, _IssuedState, bridge.VerifiedPhysicalOperationalFailoverV1V2WriterTermBridgeIntentCertificate]:
        if (
            type(value)
            is not IssuedPhysicalOperationalFailoverV1V2WriterTermBridgeCertificate
            or value._capability is not _ISSUED_CAPABILITY
        ):
            _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_ISSUED_CAPABILITY_REQUIRED")
        with _ISSUED_STATES_LOCK:
            state = _ISSUED_STATES.get(value)
            if state is None or (state.consumed and not permit_consumed):
                _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_ISSUED_CAPABILITY_REQUIRED")
            if state.facts is not self._facts:
                _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_CONFIG_MISMATCH")
        try:
            verified = bridge.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(
                value=state.canonical_certificate,
                config=self._facts.bridge_config,
                now=now,
            )
        except bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeError as exc:
            raise PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(
                "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_ISSUED_CERTIFICATE_INVALID"
            ) from exc
        if (
            value.certificate_id,
            value.certificate_sha256,
            value.intent_sha256,
            value.issued_at,
            value.expires_at,
        ) != (
            verified.certificate_id,
            verified.certificate_sha256,
            verified.intent_sha256,
            verified.issued_at,
            verified.expires_at,
        ):
            _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_ISSUED_TAMPERED")
        return value, state, verified

    def _consume_issued(
        self,
        value: IssuedPhysicalOperationalFailoverV1V2WriterTermBridgeCertificate,
    ) -> _IssuedState:
        with _ISSUED_STATES_LOCK:
            state = _ISSUED_STATES.get(value)
            if state is None or state.facts is not self._facts or state.consumed:
                _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_ISSUED_REPLAYED")
            state.consumed = True
            return state

    def issue_pre_transaction(
        self,
        *,
        writer_admission: object,
        v1_current_term_provenance: object,
        v2_prepared: object,
    ) -> IssuedPhysicalOperationalFailoverV1V2WriterTermBridgeCertificate:
        """Sign and immediately verify one certificate before a DB transaction.

        The V1 provenance handle is deliberately consumed only after the V2
        prepare has passed fresh validation.  Once consumed, any later
        cross-pin/signature failure leaves no retryable V1 authority.
        """

        opaque_admission, opaque_provenance, opaque_v2 = _opaque_inputs(
            writer_admission=writer_admission,
            v1_current_term_provenance=v1_current_term_provenance,
            v2_prepared=v2_prepared,
        )
        observed = self._now()
        try:
            v2_projection = (
                v2.project_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bridge_intent(
                    opaque_v2,
                    config=self._facts.v2_strict_writer_config,
                )
            )
        except v2.PhysicalWalV2WitnessRoundtripStrictWriterResponseError as exc:
            raise PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(
                "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_V2_PREPARED_INVALID"
            ) from exc
        try:
            provenance = (
                revalidator.consume_bound_physical_operational_failover_v1_witness_current_term_admission_provenance_for_writer_admission(
                    value=opaque_provenance,
                    writer_admission=opaque_admission,
                    config=self._facts.v1_revalidator_config,
                    now=observed,
                )
            )
        except revalidator.PhysicalOperationalFailoverV1WitnessCurrentTermRevalidatorError as exc:
            raise PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(
                "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_V1_PROVENANCE_INVALID"
            ) from exc
        intent = _raw_intent(provenance=provenance, v2_projection=v2_projection)
        expiry = _certificate_expiry(
            now=observed,
            facts=self._facts,
            provenance=provenance,
            v2_projection=v2_projection,
        )
        try:
            canonical = bridge.issue_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(
                config=self._facts.bridge_config,
                intent=intent,
                private_key=self._bridge_signer_private_key,
                now=observed,
                expires_at=expiry,
            )
            verified = bridge.verify_physical_operational_failover_v1_v2_writer_term_bridge_intent_certificate(
                value=canonical,
                config=self._facts.bridge_config,
                now=observed,
            )
        except bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeError as exc:
            raise PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(
                "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_PRETRANSACTION_CERTIFICATE_INVALID"
            ) from exc
        result = IssuedPhysicalOperationalFailoverV1V2WriterTermBridgeCertificate(
            certificate_id=verified.certificate_id,
            certificate_sha256=verified.certificate_sha256,
            intent_sha256=verified.intent_sha256,
            issued_at=verified.issued_at,
            expires_at=verified.expires_at,
            capability=_ISSUED_CAPABILITY,
        )
        with _ISSUED_STATES_LOCK:
            _ISSUED_STATES[result] = _IssuedState(
                facts=self._facts,
                canonical_certificate=canonical,
                writer_admission=opaque_admission,
                v2_prepared=opaque_v2,
            )
        return result

    def require_writer_admission_for_transaction(
        self,
        issued: object,
    ) -> admission.PhysicalOperationalFailoverV1WriterAdmission:
        """Release only issuance's exact opaque V1 admission to the SQL adapter."""

        _value, state, _verified = self._revalidated_issued(
            issued,
            now=self._now(),
        )
        if (
            type(state.writer_admission)
            is not admission.PhysicalOperationalFailoverV1WriterAdmission
            or state.writer_admission._capability is not admission._ADMISSION_CAPABILITY
        ):
            _fail("V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_V1_ADMISSION_TAMPERED")
        return state.writer_admission

    def require_v2_prepared_for_transaction(
        self,
        issued: object,
    ) -> v2.PreparedPhysicalWalV2WitnessRoundtripStrictWriterResponse:
        """Release only issuance's exact opaque V2 prepare after fresh checks."""

        _value, state, _verified = self._revalidated_issued(
            issued,
            now=self._now(),
        )
        try:
            v2.require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                state.v2_prepared,
                config=self._facts.v2_strict_writer_config,
            )
        except v2.PhysicalWalV2WitnessRoundtripStrictWriterResponseError as exc:
            raise PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(
                "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_V2_PREPARED_INVALID"
            ) from exc
        return state.v2_prepared

    def bind_post_flush(
        self,
        *,
        issued: object,
        v1_sql_commit_receipt: object,
    ) -> bridge.BoundPhysicalOperationalFailoverV1V2WriterTermBridgeIntent:
        """Consume an opaque flushed V1 receipt and return the bound bridge.

        This must be called only after the caller's V1 SQL adapter has
        successfully flushed the immutable parent row in the still-owned
        local transaction.  It intentionally has no parent input other than
        that opaque adapter receipt.
        """

        observed = self._now()
        value, state, verified = self._revalidated_issued(issued, now=observed)
        # Recheck the exact V2 capability before consuming either the issued
        # certificate or the SQL receipt.  A live V2 term/activation change is
        # therefore fenced before a final parent binding is attempted.
        try:
            v2.project_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bridge_intent(
                state.v2_prepared,
                config=self._facts.v2_strict_writer_config,
            )
        except v2.PhysicalWalV2WitnessRoundtripStrictWriterResponseError as exc:
            raise PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(
                "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_V2_PREPARED_INVALID"
            ) from exc
        self._consume_issued(value)
        try:
            parent_projection = (
                sql.require_physical_operational_failover_v1_writer_admission_sqlalchemy_commit_receipt(
                    v1_sql_commit_receipt,
                    config=self._facts.v1_sqlalchemy_transaction_config,
                )
            )
        except sql.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError as exc:
            raise PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(
                "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_V1_SQL_RECEIPT_INVALID"
            ) from exc
        parent = bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeParentReceipt(
            commit_id=str(parent_projection.commit_id),
            commit_sha256=parent_projection.commit_sha256,
            receipt_sha256=parent_projection.receipt_sha256,
            cluster_id=parent_projection.cluster_id,
            local_site=parent_projection.local_site,
            release_sha=parent_projection.release_sha,
            generation_id=parent_projection.generation_id,
            prior_revision=parent_projection.prior_revision,
            next_revision=parent_projection.next_revision,
            fence_generation=parent_projection.fence_generation,
            writer_epoch=parent_projection.writer_epoch,
            writer_lease_id=parent_projection.writer_lease_id,
            evidence_id=parent_projection.evidence_id,
            revalidation_id=parent_projection.revalidation_id,
            admitted_at=parent_projection.admitted_at,
        )
        try:
            return bridge.bind_physical_operational_failover_v1_v2_writer_term_bridge_parent(
                certificate=verified,
                parent=parent,
                config=self._facts.bridge_config,
                now=observed,
            )
        except bridge.PhysicalOperationalFailoverV1V2WriterTermBridgeError as exc:
            raise PhysicalOperationalFailoverV1V2WriterTermBridgeRuntimeIssuerError(
                "V1_V2_WRITER_TERM_BRIDGE_RUNTIME_ISSUER_PARENT_BINDING_INVALID"
            ) from exc
