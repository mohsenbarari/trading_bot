"""Root-owned PostgreSQL boundary for the Gen2 bound strict-writer response.

This adapter deliberately owns *no* database lifecycle.  Its caller supplies
an already-open, clean root ``AsyncSession`` transaction and is solely
responsible for its eventual commit or rollback.  Within that transaction the
adapter performs the only safe ordering for the Gen2 response:

1. lock the V1 head's advisory domain and reconcile the global attestation
   registry plus any Gen2 row;
2. only if no durable Gen2 outcome exists, append/flush the opaque V1 parent;
3. bind that exact parent through a narrow, synchronous, pre-issued bridge
   issuer; then locally sign, append, and flush the Gen2 row.

The adapter never starts, commits, rolls back, or closes a transaction.  It
does not open a connection, perform network/filesystem work, invoke HSMs, or
run arbitrary callbacks inside the transaction.  The only injected seam is a
small *pure* opaque bridge issuer protocol.  It exists because the issuer is
owned by the V1/V2 bridge subsystem; its two methods are capability handoffs,
not a general callback mechanism, and awaitables are rejected fail-closed.

An inserted-and-flushed row is still only ``pending_external_commit``.  The
outer owner must know that its commit succeeded before calling the explicit
post-commit finalizer.  Conversely, a missing row during reconciliation is
reported as an unknown outcome and is never silently retried with a new V1
head advance.  A global cross-generation consumption conflict is always a
hard-fence condition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import inspect
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core import physical_operational_failover_v1_writer_admission_sqlalchemy_transaction as v1_sql
from core import physical_wal_v2_witness_roundtrip_strict_writer_bound_response as bound_response
from models.physical_wal_v2_witness_roundtrip_attestation_consumption import (
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN1,
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN2,
    PhysicalWalV2WitnessRoundtripAttestationConsumption,
)
from models.physical_wal_v2_witness_roundtrip_strict_writer_bound import (
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
)


__all__ = (
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_CONTRACT",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_DEFAULT_ENABLED",
    "BoundPhysicalWalV2WitnessRoundtripStrictWriterCommitReconciliation",
    "DurablePhysicalWalV2WitnessRoundtripStrictWriterBoundCommitReconciliationRequired",
    "PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundOpaqueBridgeIssuer",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionAdapter",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionConfig",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError",
    "finalize_pending_physical_wal_v2_witness_roundtrip_strict_writer_bound_commit",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_CONTRACT = (
    "gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-bound-sqlalchemy-transaction-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_DEFAULT_ENABLED = False

_PENDING_CAPABILITY = object()
_OUTCOME_KNOWN = "known_durable"
_OUTCOME_PENDING = "pending_external_commit"
_OUTCOME_UNKNOWN = "unknown"


class PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(RuntimeError):
    """A local Gen2 transaction boundary cannot safely continue.

    ``outcome`` says what the adapter can prove about durable state, not what a
    caller hopes happened.  ``requires_hard_fence`` directs the enclosing
    writer controller to stop rather than retry a possibly competing writer.
    This module never performs that external fence itself.
    """

    def __init__(
        self,
        code: str,
        *,
        outcome: Literal["known_durable", "pending_external_commit", "unknown"] = _OUTCOME_UNKNOWN,
        requires_hard_fence: bool = False,
        reconciliation_identity: "PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity | None" = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.outcome = outcome
        self.requires_hard_fence = requires_hard_fence
        self.reconciliation_identity = reconciliation_identity


def _fail(
    code: str,
    *,
    outcome: Literal["known_durable", "pending_external_commit", "unknown"] = _OUTCOME_UNKNOWN,
    requires_hard_fence: bool = False,
    reconciliation_identity: "PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity | None" = None,
) -> None:
    raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
        code,
        outcome=outcome,
        requires_hard_fence=requires_hard_fence,
        reconciliation_identity=reconciliation_identity,
    )


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionConfig:
    """Default-off local policy; no engine, DSN, peer, or signer callback.

    The direct private key is deliberately held only as an in-process object.
    The Gen2 response module verifies that its public half matches the pinned
    local signer before creating a receipt.  An HSM, RPC signer, coroutine, or
    callable key provider is intentionally not accepted at this seam.
    """

    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_DEFAULT_ENABLED
    v1_transaction_config: v1_sql.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig | None = None
    bound_response_config: bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig | None = None
    local_commit_private_key: Ed25519PrivateKey | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@runtime_checkable
class PhysicalWalV2WitnessRoundtripStrictWriterBoundOpaqueBridgeIssuer(Protocol):
    """The intentionally narrow bridge handoff used by this SQL adapter.

    ``issued`` is an opaque pre-transaction result minted by the bridge
    subsystem.  Both methods must be synchronous, CPU-only capability checks:
    no database access, I/O, HSM call, clock extension, or transaction
    lifecycle action is permitted.  The concrete runtime issuer is kept out
    of this module so its ownership and one-shot provenance stay isolated.
    """

    def require_writer_admission_for_transaction(
        self,
        *,
        issued: object,
    ) -> object:
        """Release the exact opaque V1 writer-admission for this issued intent."""

    def require_v2_prepared_for_transaction(
        self,
        *,
        issued: object,
    ) -> object:
        """Release the exact opaque legacy V2 prepare for this issued intent."""

    def bind_post_flush(
        self,
        *,
        issued: object,
        v1_sql_commit_receipt: object,
    ) -> object:
        """Bind the opaque just-flushed V1 parent and return an opaque bridge."""


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity:
    """Serializable, non-authorizing exact Gen2 base identity for recovery.

    This contains no V1 admission, bridge capability, private key, signed
    observation, or authority.  It is purposely retained in a pending result
    so a caller that loses the outer commit response can open a *new* root
    transaction and ask whether the exact Gen2 durable row exists, even after
    ``bind_post_flush`` has consumed the original bridge issuance or after a
    process restart.  A matching row still yields only a hard-fenced
    reconciliation-required result.
    """

    schema: str
    configuration_sha256: str
    v2_base_configuration_sha256: str
    atomic_commit_boundary: str
    commit_id: str
    v2_base_commit_id: str
    attestation_sha256: str
    ir_durable_assertion_sha256: str
    context_certificate_sha256: str
    context_sha256: str
    source_envelope_sha256: str
    source_request_sha256: str
    destination_receipt_sha256: str
    durable_ledger_entry_sha256: str
    target_recovery_evidence_sha256: str
    readback_attestation_sha256: str
    stage_receipt_sha256: str
    witness_sequence: int
    witness_ledger_entry_sha256: str
    witness_ledger_previous_head_sha256: str
    witness_ledger_binding_sha256: str
    writer_holder_site: str
    writer_epoch: int
    writer_lease_id: str
    witnessed_term_proof_sha256: str
    witness_transition_id: str
    activation_mode: str
    activation_stream_generation_id: str
    activation_route_artifact_sha256: str
    activation_source_cutover_attestation_sha256: str
    activation_receiver_permit_sha256: str


@dataclass(frozen=True)
class DurablePhysicalWalV2WitnessRoundtripStrictWriterBoundCommitReconciliationRequired:
    """A matching locked durable row whose complete verifier is still required.

    This is deliberately *not* an idempotent success and cannot mint a Gen2
    observation, readiness signal, V1 writer admission, or active-writer
    authority.  The structural checks done here only establish that the
    registry and direct Gen2 base pins agree and that the stored runtime
    receipt's digest agrees with its bytes.  A restart-safe verifier still has
    to reconstruct and validate the signed bridge/runtime receipt, V1 parent
    row/hash/head history, certificate/term time bounds, and all cross-pins.
    Until that verifier exists, the enclosing writer controller must hard
    fence rather than treat this as an idempotent response.
    """

    outcome: Literal["known_durable"]
    commit_id: str
    v2_base_commit_id: str
    attestation_sha256: str
    runtime_commit_receipt_sha256: str
    canonical_runtime_receipt: bytes
    canonical_v1_v2_writer_term_bridge_certificate: bytes
    committed_at: datetime
    reconciliation_identity: PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity
    reconciliation_required: bool = True
    requires_hard_fence: bool = True


@dataclass(frozen=True, eq=False, init=False)
class PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit:
    """A flushed Gen2 insert that still awaits the outer transaction commit."""

    outcome: Literal["pending_external_commit"]
    instruction: bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction
    runtime_receipt: bytes
    reconciliation_identity: PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity
    _bound_response: object | None = field(default=None, init=False, repr=False, compare=False)
    _capability: object | None = field(default=None, init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        instruction: bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
        runtime_receipt: bytes,
        reconciliation_identity: PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity,
        bound: object,
        capability: object,
    ) -> None:
        if capability is not _PENDING_CAPABILITY:
            raise TypeError(
                "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_PENDING_CONSTRUCTION_FORBIDDEN"
            )
        object.__setattr__(self, "outcome", _OUTCOME_PENDING)
        object.__setattr__(self, "instruction", instruction)
        object.__setattr__(self, "runtime_receipt", runtime_receipt)
        object.__setattr__(self, "reconciliation_identity", reconciliation_identity)
        object.__setattr__(self, "_bound_response", bound)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_PENDING_SERIALIZATION_FORBIDDEN"
        )


@dataclass(frozen=True)
class BoundPhysicalWalV2WitnessRoundtripStrictWriterCommitReconciliation:
    """Typed result for a new root transaction after an uncertain outer commit."""

    outcome: Literal["known_durable", "unknown"]
    durable_row: DurablePhysicalWalV2WitnessRoundtripStrictWriterBoundCommitReconciliationRequired | None
    requires_hard_fence: bool


@dataclass(frozen=True)
class _Facts:
    v1_transaction_config: v1_sql.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig
    bound_response_config: bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig
    local_commit_private_key: Ed25519PrivateKey
    v1_binding: object


# The model deliberately has a different column name for the response schema.
_BASE_ROW_FIELDS: tuple[tuple[str, str], ...] = (
    ("instruction_schema", "schema"),
    ("configuration_sha256", "configuration_sha256"),
    ("v2_base_configuration_sha256", "v2_base_configuration_sha256"),
    ("atomic_commit_boundary", "atomic_commit_boundary"),
    ("commit_id", "commit_id"),
    ("v2_base_commit_id", "v2_base_commit_id"),
    ("attestation_sha256", "attestation_sha256"),
    ("ir_durable_assertion_sha256", "ir_durable_assertion_sha256"),
    ("context_certificate_sha256", "context_certificate_sha256"),
    ("context_sha256", "context_sha256"),
    ("source_envelope_sha256", "source_envelope_sha256"),
    ("source_request_sha256", "source_request_sha256"),
    ("destination_receipt_sha256", "destination_receipt_sha256"),
    ("durable_ledger_entry_sha256", "durable_ledger_entry_sha256"),
    ("target_recovery_evidence_sha256", "target_recovery_evidence_sha256"),
    ("readback_attestation_sha256", "readback_attestation_sha256"),
    ("stage_receipt_sha256", "stage_receipt_sha256"),
    ("witness_sequence", "witness_sequence"),
    ("witness_ledger_entry_sha256", "witness_ledger_entry_sha256"),
    ("witness_ledger_previous_head_sha256", "witness_ledger_previous_head_sha256"),
    ("witness_ledger_binding_sha256", "witness_ledger_binding_sha256"),
    ("writer_holder_site", "writer_holder_site"),
    ("writer_epoch", "writer_epoch"),
    ("writer_lease_id", "writer_lease_id"),
    ("witnessed_term_proof_sha256", "witnessed_term_proof_sha256"),
    ("witness_transition_id", "witness_transition_id"),
    ("activation_mode", "activation_mode"),
    ("activation_stream_generation_id", "activation_stream_generation_id"),
    ("activation_route_artifact_sha256", "activation_route_artifact_sha256"),
    ("activation_source_cutover_attestation_sha256", "activation_source_cutover_attestation_sha256"),
    ("activation_receiver_permit_sha256", "activation_receiver_permit_sha256"),
)


def _reconciliation_identity_from_base(
    base: bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundBaseInstruction,
) -> PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity:
    """Release only durable-lookup pins from a freshly opaque base prepare."""

    try:
        return PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity(
            **{
                field_name: getattr(base, field_name)
                for field_name in PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity.__dataclass_fields__
            }
        )
    except (AttributeError, TypeError) as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_PREPARED_RESPONSE_INVALID"
        ) from exc


def _require_reconciliation_identity(
    value: object,
) -> PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity:
    """Validate enough shape to make recovery lookup bounded and fail-closed.

    This validation deliberately does not turn caller-rebuilt identity data
    into authority.  It only prevents an arbitrary object from becoming a
    database lookup key; every positive match remains reconciliation-required
    and hard-fenced.
    """

    if type(value) is not PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_RECONCILIATION_IDENTITY_INVALID")
    string_fields = tuple(
        name
        for name in PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity.__dataclass_fields__
        if name not in {"witness_sequence", "writer_epoch"}
    )
    try:
        if any(
            type(getattr(value, name)) is not str
            or not 1 <= len(getattr(value, name)) <= 512
            for name in string_fields
        ):
            _fail(
                "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_RECONCILIATION_IDENTITY_INVALID"
            )
        if type(value.witness_sequence) is not int or value.witness_sequence < 1:
            _fail(
                "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_RECONCILIATION_IDENTITY_INVALID"
            )
        if type(value.writer_epoch) is not int or value.writer_epoch < 1:
            _fail(
                "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_RECONCILIATION_IDENTITY_INVALID"
            )
    except PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError:
        raise
    except (AttributeError, TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_RECONCILIATION_IDENTITY_INVALID"
        ) from exc
    return value


def _facts(
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionConfig,
) -> _Facts | None:
    if type(config) is not PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionConfig:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_CONFIG_INVALID")
    if config.enabled is False:
        return None
    if (
        config.enabled is not True
        or type(config.v1_transaction_config)
        is not v1_sql.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig
        or type(config.bound_response_config)
        is not bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseConfig
        or not isinstance(config.local_commit_private_key, Ed25519PrivateKey)
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_CONFIG_INVALID")
    try:
        v1_facts = v1_sql._facts(config.v1_transaction_config)
        # Validate all Gen2 role/key pins before touching the supplied session.
        bound_response._config(config.bound_response_config)
    except (
        v1_sql.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError,
        bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError,
        AttributeError,
        TypeError,
        ValueError,
    ) as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_CONFIG_INVALID"
        ) from exc
    if v1_facts is None:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_CONFIG_INVALID")
    return _Facts(
        v1_transaction_config=config.v1_transaction_config,
        bound_response_config=config.bound_response_config,
        local_commit_private_key=config.local_commit_private_key,
        v1_binding=v1_facts.binding,
    )


def _require_clean_external_root_transaction(session: object) -> None:
    """Enforce a caller-opened clean PostgreSQL root transaction.

    The generic V1 adapter checks an active clean transaction too.  This
    stricter front gate additionally rejects nested/savepoint work before the
    Gen2 registry lookup, so an outer unrelated write cannot later commit a
    partial parent/response boundary.
    """

    active = getattr(session, "in_transaction", None)
    transaction = getattr(session, "get_transaction", None)
    nested_transaction = getattr(session, "get_nested_transaction", None)
    get_bind = getattr(session, "get_bind", None)
    if not all(callable(item) for item in (active, transaction, nested_transaction, get_bind)):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
    try:
        if active() is not True:
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_REQUIRED")
        root = transaction()
        nested = nested_transaction()
        bind = get_bind()
    except PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError:
        raise
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_SESSION_INVALID"
        ) from exc
    if root is None:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_REQUIRED")
    if nested is not None or getattr(root, "nested", False) is True:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_ROOT_REQUIRED")
    if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_POSTGRES_REQUIRED")
    for name in ("new", "dirty", "deleted", "identity_map"):
        pending = getattr(session, name, None)
        if pending is None:
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
        try:
            if bool(pending):
                _fail(
                    "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_UNGUARDED_PENDING_MUTATION"
                )
        except PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError:
            raise
        except Exception as exc:
            raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
                "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_SESSION_INVALID"
            ) from exc


async def _execute(session: object, statement: object, *, code: str) -> object:
    execute = getattr(session, "execute", None)
    if not callable(execute):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
    try:
        result = execute(statement)
        if not inspect.isawaitable(result):
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
        return await result
    except PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError:
        raise
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            code,
            outcome=_OUTCOME_UNKNOWN,
            requires_hard_fence=True,
        ) from exc


async def _flush_gen2(
    session: object,
    *,
    reconciliation_identity: PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity,
) -> None:
    flush = getattr(session, "flush", None)
    if not callable(flush):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
    try:
        result = flush()
        if not inspect.isawaitable(result):
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
        await result
    except PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError:
        raise
    except IntegrityError as exc:
        text = " ".join((str(exc), str(getattr(exc, "orig", "")))).lower()
        code = (
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_GLOBAL_ATTESTATION_CONSUMPTION_CONFLICT_HARD_FENCE"
            if (
                "attestation_consumptions" in text
                or "v2wsrc_registry" in text
                or "ck_v2wsrc_registry" in text
            )
            else "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_GEN2_FLUSH_OUTCOME_UNKNOWN_HARD_FENCE"
        )
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            code,
            outcome=_OUTCOME_UNKNOWN,
            requires_hard_fence=True,
            reconciliation_identity=reconciliation_identity,
        ) from exc
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_GEN2_FLUSH_OUTCOME_UNKNOWN_HARD_FENCE",
            outcome=_OUTCOME_UNKNOWN,
            requires_hard_fence=True,
            reconciliation_identity=reconciliation_identity,
        ) from exc


def _add(
    session: object,
    value: object,
    *,
    reconciliation_identity: PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity,
) -> None:
    add = getattr(session, "add", None)
    if not callable(add):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_SESSION_INVALID")
    try:
        result = add(value)
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_GEN2_INSERT_FAILED",
            outcome=_OUTCOME_UNKNOWN,
            requires_hard_fence=True,
            reconciliation_identity=reconciliation_identity,
        ) from exc
    if inspect.isawaitable(result):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_SESSION_INVALID")


def _scalar_one_or_none(result: object, *, code: str) -> object | None:
    callback = getattr(result, "scalar_one_or_none", None)
    if not callable(callback):
        _fail(code)
    try:
        return callback()
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(code) from exc


def _all_scalars(result: object, *, code: str) -> list[object]:
    scalars = getattr(result, "scalars", None)
    if not callable(scalars):
        _fail(code)
    try:
        values = scalars()
        all_values = getattr(values, "all", None)
        if not callable(all_values):
            _fail(code)
        result_values = all_values()
    except PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError:
        raise
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(code) from exc
    if type(result_values) is not list:
        _fail(code)
    return result_values


def _call_issuer(
    issuer: object,
    method_name: str,
    reconciliation_identity: PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity | None = None,
    **kwargs: object,
) -> object:
    """Call only a named synchronous opaque capability handoff.

    A coroutine, future, generator-like awaitable, or missing method is not a
    harmless alternate implementation: it could hide a remote call while the
    local root transaction is open, so reject it rather than await it.
    """

    callback = getattr(issuer, method_name, None)
    if not callable(callback):
        _fail(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_OPAQUE_ISSUER_INVALID",
            reconciliation_identity=reconciliation_identity,
        )
    try:
        value = callback(**kwargs)
    except PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError:
        raise
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_OPAQUE_ISSUER_REJECTED",
            outcome=_OUTCOME_UNKNOWN,
            requires_hard_fence=True,
            reconciliation_identity=reconciliation_identity,
        ) from exc
    if inspect.isawaitable(value):
        _fail(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_OPAQUE_ISSUER_ASYNC_FORBIDDEN",
            requires_hard_fence=True,
            reconciliation_identity=reconciliation_identity,
        )
    return value


def _prepare_from_issued(
    *,
    issuer: PhysicalWalV2WitnessRoundtripStrictWriterBoundOpaqueBridgeIssuer,
    issued_bridge: object,
    facts: _Facts,
) -> object:
    """Create the Gen2 opaque base prepare inside the root transaction.

    The issuer retains the legacy V2 capability from pre-transaction issuance;
    the adapter neither accepts it from a caller nor reads scalar V2 fields.
    ``prepare`` is pure and only revalidates that opaque capability under the
    pinned bound-response configuration.
    """

    legacy_prepared = _call_issuer(
        issuer,
        "require_v2_prepared_for_transaction",
        issued=issued_bridge,
    )
    try:
        return bound_response.prepare_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
            config=facts.bound_response_config,
            v2_prepared=legacy_prepared,
        )
    except bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_PREPARED_RESPONSE_INVALID"
        ) from exc


def _local_response_ids(
    instruction: bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
) -> tuple[str, str]:
    """Derive retry-stable, distinct local receipt identities from Gen2 id."""

    try:
        material = instruction.commit_id.encode("ascii", "strict")
    except (AttributeError, UnicodeEncodeError) as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_INSTRUCTION_INVALID"
        ) from exc
    digest = hashlib.sha256(
        PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_CONTRACT.encode("ascii")
        + b"\x00"
        + material
    ).hexdigest()
    return (
        "v2-g2-local-commit-" + digest,
        "v2-g2-local-response-" + digest,
    )


def _utc_now() -> datetime:
    """A local second-resolution timestamp accepted by the Gen2 signer."""

    return datetime.now(timezone.utc).replace(microsecond=0)


def _base_matches_row(
    row: object,
    base: PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity,
) -> bool:
    if type(row) is not PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit:
        return False
    try:
        for row_name, instruction_name in _BASE_ROW_FIELDS:
            if getattr(row, row_name) != getattr(base, instruction_name):
                return False
        receipt = row.canonical_runtime_receipt
        if type(receipt) is not bytes or not receipt:
            return False
        if hashlib.sha256(receipt).hexdigest() != row.runtime_commit_receipt_sha256:
            return False
        if row.attestation_consumption_id != "v2-witness-consume-g2-" + base.attestation_sha256:
            return False
        if type(row.committed_at) is not datetime or row.committed_at.tzinfo is None:
            return False
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def _reconciliation_required_from_row(
    row: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
    *,
    reconciliation_identity: PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity,
) -> DurablePhysicalWalV2WitnessRoundtripStrictWriterBoundCommitReconciliationRequired:
    return DurablePhysicalWalV2WitnessRoundtripStrictWriterBoundCommitReconciliationRequired(
        outcome=_OUTCOME_KNOWN,
        commit_id=row.commit_id,
        v2_base_commit_id=row.v2_base_commit_id,
        attestation_sha256=row.attestation_sha256,
        runtime_commit_receipt_sha256=row.runtime_commit_receipt_sha256,
        canonical_runtime_receipt=row.canonical_runtime_receipt,
        canonical_v1_v2_writer_term_bridge_certificate=(
            row.canonical_v1_v2_writer_term_bridge_certificate
        ),
        committed_at=row.committed_at,
        reconciliation_identity=reconciliation_identity,
    )


async def _reconcile_locked(
    *,
    session: object,
    base: PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity,
) -> DurablePhysicalWalV2WitnessRoundtripStrictWriterBoundCommitReconciliationRequired | None:
    """Read both durable identities while the V1 head advisory domain is held."""

    registry_result = await _execute(
        session,
        select(PhysicalWalV2WitnessRoundtripAttestationConsumption)
        .where(
            PhysicalWalV2WitnessRoundtripAttestationConsumption.attestation_sha256
            == base.attestation_sha256
        )
        .with_for_update(),
        code="V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_REGISTRY_LOOKUP_FAILED",
    )
    registry = _scalar_one_or_none(
        registry_result,
        code="V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_REGISTRY_INVALID",
    )
    rows_result = await _execute(
        session,
        select(PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit)
        .where(
            or_(
                PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit.attestation_sha256
                == base.attestation_sha256,
                PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit.commit_id
                == base.commit_id,
                PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit.v2_base_commit_id
                == base.v2_base_commit_id,
            )
        )
        .with_for_update(),
        code="V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_GEN2_LOOKUP_FAILED",
    )
    rows = _all_scalars(
        rows_result,
        code="V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_GEN2_LOOKUP_INVALID",
    )
    if registry is None and not rows:
        return None
    if type(registry) is not PhysicalWalV2WitnessRoundtripAttestationConsumption:
        _fail(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_REGISTRY_OR_GEN2_INCONSISTENT_HARD_FENCE",
            requires_hard_fence=True,
        )
    if registry.source_generation == PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN1:
        _fail(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_CROSS_GENERATION_ATTESTATION_CONFLICT_HARD_FENCE",
            requires_hard_fence=True,
        )
    if registry.source_generation != PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN2:
        _fail(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_REGISTRY_OR_GEN2_INCONSISTENT_HARD_FENCE",
            requires_hard_fence=True,
        )
    if len(rows) != 1 or not _base_matches_row(rows[0], base):
        _fail(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_REGISTRY_OR_GEN2_INCONSISTENT_HARD_FENCE",
            requires_hard_fence=True,
        )
    row = rows[0]
    assert type(row) is PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit
    if registry.source_commit_id != row.commit_id:
        _fail(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_REGISTRY_OR_GEN2_INCONSISTENT_HARD_FENCE",
            requires_hard_fence=True,
        )
    return _reconciliation_required_from_row(
        row,
        reconciliation_identity=base,
    )


def _model_row(
    instruction: bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundCommitInstruction,
    *,
    runtime_receipt: bytes,
    committed_at: datetime,
) -> PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit:
    """Map every durable Gen2 field explicitly; never copy arbitrary attrs."""

    try:
        parent_id = UUID(instruction.v1_writer_admission_commit_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_INSTRUCTION_INVALID"
        ) from exc
    return PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit(
        instruction_schema=instruction.schema,
        configuration_sha256=instruction.configuration_sha256,
        v2_base_configuration_sha256=instruction.v2_base_configuration_sha256,
        atomic_commit_boundary=instruction.atomic_commit_boundary,
        commit_id=instruction.commit_id,
        v2_base_commit_id=instruction.v2_base_commit_id,
        attestation_sha256=instruction.attestation_sha256,
        attestation_consumption_id="v2-witness-consume-g2-" + instruction.attestation_sha256,
        ir_durable_assertion_sha256=instruction.ir_durable_assertion_sha256,
        context_certificate_sha256=instruction.context_certificate_sha256,
        context_sha256=instruction.context_sha256,
        source_envelope_sha256=instruction.source_envelope_sha256,
        source_request_sha256=instruction.source_request_sha256,
        destination_receipt_sha256=instruction.destination_receipt_sha256,
        durable_ledger_entry_sha256=instruction.durable_ledger_entry_sha256,
        target_recovery_evidence_sha256=instruction.target_recovery_evidence_sha256,
        readback_attestation_sha256=instruction.readback_attestation_sha256,
        stage_receipt_sha256=instruction.stage_receipt_sha256,
        witness_sequence=instruction.witness_sequence,
        witness_ledger_entry_sha256=instruction.witness_ledger_entry_sha256,
        witness_ledger_previous_head_sha256=instruction.witness_ledger_previous_head_sha256,
        witness_ledger_binding_sha256=instruction.witness_ledger_binding_sha256,
        writer_holder_site=instruction.writer_holder_site,
        writer_epoch=instruction.writer_epoch,
        writer_lease_id=instruction.writer_lease_id,
        witnessed_term_proof_sha256=instruction.witnessed_term_proof_sha256,
        witness_transition_id=instruction.witness_transition_id,
        activation_mode=instruction.activation_mode,
        activation_stream_generation_id=instruction.activation_stream_generation_id,
        activation_route_artifact_sha256=instruction.activation_route_artifact_sha256,
        activation_source_cutover_attestation_sha256=instruction.activation_source_cutover_attestation_sha256,
        activation_receiver_permit_sha256=instruction.activation_receiver_permit_sha256,
        v1_parent_cluster_id=instruction.v1_parent_cluster_id,
        v1_parent_local_site=instruction.v1_parent_local_site,
        v1_parent_release_sha=instruction.v1_parent_release_sha,
        v1_parent_generation_id=instruction.v1_parent_generation_id,
        v1_writer_admission_commit_id=parent_id,
        v1_writer_admission_commit_sha256=instruction.v1_writer_admission_commit_sha256,
        v1_writer_admission_receipt_sha256=instruction.v1_writer_admission_receipt_sha256,
        v1_parent_prior_revision=instruction.v1_parent_prior_revision,
        v1_parent_next_revision=instruction.v1_parent_next_revision,
        v1_parent_fence_generation=instruction.v1_parent_fence_generation,
        v1_parent_holder_site=instruction.v1_parent_holder_site,
        v1_parent_evidence_id=instruction.v1_parent_evidence_id,
        v1_parent_revalidation_id=instruction.v1_parent_revalidation_id,
        v1_parent_writer_epoch=instruction.v1_parent_writer_epoch,
        v1_parent_writer_lease_id=instruction.v1_parent_writer_lease_id,
        v1_parent_term_issued_at=instruction.v1_parent_term_issued_at,
        v1_parent_term_expires_at=instruction.v1_parent_term_expires_at,
        v1_parent_admitted_at=instruction.v1_parent_admitted_at,
        v1_v2_writer_term_bridge_certificate_id=instruction.v1_v2_writer_term_bridge_certificate_id,
        v1_v2_writer_term_bridge_intent_sha256=instruction.v1_v2_writer_term_bridge_intent_sha256,
        v1_v2_writer_term_bridge_certificate_sha256=instruction.v1_v2_writer_term_bridge_certificate_sha256,
        v1_v2_writer_term_bridge_parent_binding_sha256=instruction.v1_v2_writer_term_bridge_parent_binding_sha256,
        canonical_v1_v2_writer_term_bridge_certificate=instruction.canonical_v1_v2_writer_term_bridge_certificate,
        local_commit_record_id=_local_response_ids(instruction)[0],
        local_response_id=_local_response_ids(instruction)[1],
        canonical_runtime_receipt=runtime_receipt,
        runtime_commit_receipt_sha256=hashlib.sha256(runtime_receipt).hexdigest(),
        committed_at=committed_at,
    )


class PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionAdapter:
    """Prepare/reconcile one Gen2 bound row in an externally-owned root tx."""

    def __init__(
        self,
        config: PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionConfig,
    ) -> None:
        self._config = config

    async def reconcile_after_unknown_outcome(
        self,
        *,
        session: AsyncSession,
        reconciliation_identity: PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity,
    ) -> BoundPhysicalWalV2WitnessRoundtripStrictWriterCommitReconciliation | None:
        """Recheck one exact Gen2 base in a fresh root tx without touching V1.

        This accepts the non-authorizing identity retained in a pending result,
        not an issued bridge capability: ``bind_post_flush`` may have consumed
        the latter before an outer commit response became unknown.  An absent
        row is deliberately returned as an ``unknown`` hard-fence outcome
        rather than authorization to reuse old issued material.
        """

        facts = _facts(self._config)
        if facts is None:
            return None
        _require_clean_external_root_transaction(session)
        base = _require_reconciliation_identity(reconciliation_identity)
        await _execute(
            session,
            select(
                func.pg_advisory_xact_lock(
                    v1_sql.physical_operational_failover_v1_writer_admission_head_advisory_lock_key(
                        facts.v1_binding
                    )
                )
            ),
            code="V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_LOCK_FAILED",
        )
        known = await _reconcile_locked(session=session, base=base)
        if known is not None:
            return BoundPhysicalWalV2WitnessRoundtripStrictWriterCommitReconciliation(
                outcome=_OUTCOME_KNOWN,
                durable_row=known,
                requires_hard_fence=True,
            )
        return BoundPhysicalWalV2WitnessRoundtripStrictWriterCommitReconciliation(
            outcome=_OUTCOME_UNKNOWN,
            durable_row=None,
            requires_hard_fence=True,
        )

    async def persist_bound_writer_response(
        self,
        *,
        session: AsyncSession,
        issued_bridge: object,
        issuer: PhysicalWalV2WitnessRoundtripStrictWriterBoundOpaqueBridgeIssuer,
    ) -> (
        DurablePhysicalWalV2WitnessRoundtripStrictWriterBoundCommitReconciliationRequired
        | PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit
        | None
    ):
        """Flush a Gen2 response only after exact pre-V1 reconciliation.

        ``issued_bridge`` must have been issued and verified before this
        transaction.  The adapter intentionally does not accept raw V1 parent
        fields, bridge certificates, V2 instructions, or a caller-provided
        runtime receipt.
        """

        facts = _facts(self._config)
        if facts is None:
            return None
        if issued_bridge is None:
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_ISSUED_BRIDGE_REQUIRED")
        _require_clean_external_root_transaction(session)
        prepared_response = _prepare_from_issued(
            issuer=issuer,
            issued_bridge=issued_bridge,
            facts=facts,
        )
        try:
            base = bound_response.require_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
                prepared_response,
                config=facts.bound_response_config,
            )
        except bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError as exc:
            raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
                "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_PREPARED_RESPONSE_INVALID"
            ) from exc
        reconciliation_identity = _reconciliation_identity_from_base(base)
        await _execute(
            session,
            select(
                func.pg_advisory_xact_lock(
                    v1_sql.physical_operational_failover_v1_writer_admission_head_advisory_lock_key(
                        facts.v1_binding
                    )
                )
            ),
            code="V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_LOCK_FAILED",
        )
        known = await _reconcile_locked(session=session, base=reconciliation_identity)
        if known is not None:
            return known

        # The V2 preparation accessor above is a non-consuming preflight
        # check.  Do not release the V1 admission or consume the issued bridge
        # through ``bind_post_flush`` until reconciliation proves this exact
        # Gen2 identity absent.
        writer_admission = _call_issuer(
            issuer,
            "require_writer_admission_for_transaction",
            reconciliation_identity=reconciliation_identity,
            issued=issued_bridge,
        )
        v1_adapter = v1_sql.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionAdapter(
            facts.v1_transaction_config
        )
        try:
            v1_receipt = await v1_adapter.persist_writer_admission(
                session=session,
                writer_admission=writer_admission,
            )
        except v1_sql.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError as exc:
            raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
                "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_V1_PARENT_PERSIST_FAILED",
                outcome=_OUTCOME_UNKNOWN,
                requires_hard_fence=True,
                reconciliation_identity=reconciliation_identity,
            ) from exc
        if v1_receipt is None:
            _fail(
                "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_V1_PARENT_RECEIPT_REQUIRED",
                requires_hard_fence=True,
                reconciliation_identity=reconciliation_identity,
            )
        bridge_bound = _call_issuer(
            issuer,
            "bind_post_flush",
            reconciliation_identity=reconciliation_identity,
            issued=issued_bridge,
            v1_sql_commit_receipt=v1_receipt,
        )
        try:
            bound = bound_response.bind_prepared_physical_wal_v2_witness_roundtrip_strict_writer_bound_response(
                prepared_response,
                bridge_bound=bridge_bound,
                config=facts.bound_response_config,
            )
            instruction = bound_response.require_bound_prepared_physical_wal_v2_witness_roundtrip_strict_writer_response(
                bound,
                config=facts.bound_response_config,
            )
        except bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError as exc:
            raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
                "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_POST_V1_BINDING_INVALID",
                outcome=_OUTCOME_UNKNOWN,
                requires_hard_fence=True,
                reconciliation_identity=reconciliation_identity,
            ) from exc
        # The bridge binder must not substitute a different V2 base after the
        # pre-head reconciliation.  Compare every base pin again before Gen2
        # row construction; the full response verifier covers V1/bridge pins.
        if any(
            getattr(instruction, instruction_name) != getattr(reconciliation_identity, instruction_name)
            for _row_name, instruction_name in _BASE_ROW_FIELDS
            if instruction_name != "schema"
        ) or instruction.schema != reconciliation_identity.schema:
            _fail(
                "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_POST_V1_BASE_SUBSTITUTION_HARD_FENCE",
                requires_hard_fence=True,
                reconciliation_identity=reconciliation_identity,
            )
        committed_at = _utc_now()
        local_commit_record_id, local_response_id = _local_response_ids(instruction)
        try:
            runtime_receipt = bound_response.sign_bound_physical_wal_v2_witness_roundtrip_strict_writer_runtime_receipt(
                bound,
                config=facts.bound_response_config,
                local_commit_private_key=facts.local_commit_private_key,
                local_commit_record_id=local_commit_record_id,
                local_response_id=local_response_id,
                committed_at=committed_at,
            )
        except bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError as exc:
            raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
                "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_LOCAL_SIGNING_INVALID",
                outcome=_OUTCOME_UNKNOWN,
                requires_hard_fence=True,
                reconciliation_identity=reconciliation_identity,
            ) from exc
        try:
            row = _model_row(
                instruction,
                runtime_receipt=runtime_receipt,
                committed_at=committed_at,
            )
        except PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError as exc:
            raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
                exc.code,
                outcome=exc.outcome,
                requires_hard_fence=True,
                reconciliation_identity=reconciliation_identity,
            ) from exc
        _add(
            session,
            row,
            reconciliation_identity=reconciliation_identity,
        )
        # The source-table trigger claims the shared registry in this same
        # flush.  A collision rolls the *outer* transaction back; this adapter
        # never tries to repair/retry it inside the transaction.
        await _flush_gen2(
            session,
            reconciliation_identity=reconciliation_identity,
        )
        return PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit(
            instruction=instruction,
            runtime_receipt=runtime_receipt,
            reconciliation_identity=reconciliation_identity,
            bound=bound,
            capability=_PENDING_CAPABILITY,
        )


def finalize_pending_physical_wal_v2_witness_roundtrip_strict_writer_bound_commit(
    pending: object,
    *,
    config: PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionConfig,
) -> bound_response.VerifiedPhysicalWalV2WitnessRoundtripStrictWriterBoundResponseObservation | None:
    """Finalize only after the caller knows its outer transaction committed.

    This is intentionally a pure post-commit verification.  It cannot make a
    failed/unknown database commit successful, and callers must not invoke it
    after rollback or merely after ``flush``.
    """

    facts = _facts(config)
    if facts is None:
        return None
    if (
        type(pending) is not PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit
        or pending._capability is not _PENDING_CAPABILITY
        or pending._bound_response is None
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_PENDING_CAPABILITY_REQUIRED")
    try:
        return bound_response.finalize_bound_physical_wal_v2_witness_roundtrip_strict_writer_response(
            pending._bound_response,
            config=facts.bound_response_config,
            runtime_receipt=pending.runtime_receipt,
        )
    except bound_response.PhysicalWalV2WitnessRoundtripStrictWriterBoundResponseError as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError(
            "V2_WITNESS_STRICT_WRITER_BOUND_SQLALCHEMY_TRANSACTION_POST_COMMIT_RECEIPT_INVALID_HARD_FENCE",
            outcome=_OUTCOME_UNKNOWN,
            requires_hard_fence=True,
        ) from exc
