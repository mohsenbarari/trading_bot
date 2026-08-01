"""Default-off root transaction envelope for the Gen2 bound writer boundary.

The underlying Gen2 SQL adapter deliberately does not own transaction
lifecycle.  This module is the separate, opt-in owner for one *fresh*
``AsyncSession`` root transaction when an application is eventually wired to
the Gen2 boundary.  It does not change routes, create sessions/engines, talk
to Witness/Object Storage, invoke an HSM, or retry/reconcile a failure.

The only permitted order is:

* receive a bridge capability that was issued before PostgreSQL work;
* begin exactly one clean PostgreSQL root transaction;
* call the reviewed Gen2 adapter before yielding any business-DML facade;
* commit exactly once after normal business exit; and
* finalize the locally signed response only after that commit returns.

If a flush or commit outcome is uncertain, the public error exposes only the
non-authorizing reconciliation identity and requires an external hard fence.
It never releases an observation, retries the transaction, or calls the
adapter's reconciliation path automatically.  A pre-existing durable Gen2
row is likewise not an idempotent success: it is returned only as a
hard-fenced reconciliation-required error before business DML is exposed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import inspect
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.sql.selectable import CompoundSelect, Select
from sqlalchemy.sql.visitors import iterate

from core import application_writer_transaction_envelope_guard as application_envelope_guard
from core import physical_wal_v2_witness_roundtrip_strict_writer_bound_sqlalchemy_transaction as transaction_adapter
from models.operational_writer_admission import (
    OperationalWriterAdmissionCommit,
    OperationalWriterAdmissionHead,
)
from models.physical_wal_v2_witness_roundtrip_attestation_consumption import (
    PhysicalWalV2WitnessRoundtripAttestationConsumption,
)
from models.physical_wal_v2_witness_roundtrip_strict_writer import (
    PhysicalWalV2WitnessRoundtripStrictWriterCommit,
)
from models.physical_wal_v2_witness_roundtrip_strict_writer_bound import (
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
)


__all__ = (
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_CONTRACT",
    "PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_DEFAULT_ENABLED",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundBusinessSession",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelope",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeConfig",
    "PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError",
)


PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_CONTRACT = (
    "gold-trade-physical-wal-v2-witness-roundtrip-strict-writer-bound-transaction-envelope-v1"
)
PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_DEFAULT_ENABLED = False

_CONSUMED_SESSION_INFO_KEY = object()


class PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(RuntimeError):
    """This one Gen2 root transaction must stop rather than improvise.

    ``reconciliation_identity`` is intentionally the only recovery material
    released for unknown durable outcome.  It contains only non-authorizing
    Gen2 lookup pins; it is not a writer, bridge, receipt, or observation.
    """

    def __init__(
        self,
        code: str,
        *,
        outcome: Literal["known_durable", "pending_external_commit", "unknown"] = "unknown",
        requires_hard_fence: bool = False,
        reconciliation_identity: transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity
        | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.outcome = outcome
        self.requires_hard_fence = requires_hard_fence
        self.reconciliation_identity = reconciliation_identity


def _fail(
    code: str,
    *,
    outcome: Literal["known_durable", "pending_external_commit", "unknown"] = "unknown",
    requires_hard_fence: bool = False,
    reconciliation_identity: transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity
    | None = None,
) -> None:
    raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
        code,
        outcome=outcome,
        requires_hard_fence=requires_hard_fence,
        reconciliation_identity=reconciliation_identity,
    )


@dataclass(frozen=True)
class PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeConfig:
    """Explicit default-off composition with no engine, session factory, or I/O."""

    enabled: bool = PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_DEFAULT_ENABLED
    sqlalchemy_transaction_config: transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionConfig | None = None


def _require_config(
    value: object,
) -> PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeConfig:
    if type(value) is not PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeConfig:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_CONFIG_INVALID")
    if value.enabled is False:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_DISABLED")
    if (
        value.enabled is not True
        or type(value.sqlalchemy_transaction_config)
        is not transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionConfig
        or value.sqlalchemy_transaction_config.enabled is not True
    ):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_CONFIG_INVALID")
    return value


def _session_info(session: object) -> dict[object, object]:
    info = getattr(session, "info", None)
    if not isinstance(info, dict):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
    return info


def _session_in_transaction(session: object) -> bool:
    callback = getattr(session, "in_transaction", None)
    if not callable(callback):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
    try:
        value = callback()
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID"
        ) from exc
    if type(value) is not bool:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
    return value


def _session_is_healthy(session: object) -> bool:
    value = getattr(session, "is_active", None)
    if type(value) is not bool:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
    return value


def _require_empty_session_state(session: object) -> None:
    for attribute in ("new", "dirty", "deleted", "identity_map"):
        value = getattr(session, attribute, None)
        if value is None:
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
        try:
            if bool(value):
                _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_PENDING_MUTATION")
        except PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError:
            raise
        except Exception as exc:
            raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
                "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID"
            ) from exc


def _require_postgresql(session: object) -> None:
    get_bind = getattr(session, "get_bind", None)
    if not callable(get_bind):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
    try:
        bind = get_bind()
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID"
        ) from exc
    if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_POSTGRES_REQUIRED")


def _require_fresh_session(session: object) -> dict[object, object]:
    info = _session_info(session)
    if _CONSUMED_SESSION_INFO_KEY in info:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_REUSED")
    if _session_in_transaction(session):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_NOT_FRESH")
    if _session_is_healthy(session) is not True:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_TERMINAL_STATE")
    _require_empty_session_state(session)
    _require_postgresql(session)
    return info


def _reserve_session(info: dict[object, object]) -> object:
    marker = object()
    info[_CONSUMED_SESSION_INFO_KEY] = marker
    return marker


def _require_reserved_session_still_fresh(session: object, *, marker: object) -> None:
    if _session_info(session).get(_CONSUMED_SESSION_INFO_KEY) is not marker:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_REUSED")
    if _session_in_transaction(session):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_NOT_FRESH")
    if _session_is_healthy(session) is not True:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_TERMINAL_STATE")
    _require_empty_session_state(session)
    _require_postgresql(session)


def _require_transaction_shape(transaction: object) -> None:
    if getattr(transaction, "nested", None) is not False:
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")
    for method in ("commit", "rollback"):
        if not callable(getattr(transaction, method, None)):
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")


def _transaction_is_live(session: object, transaction: object) -> bool:
    try:
        return _session_in_transaction(session) and getattr(transaction, "is_active") is True
    except PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError:
        return False
    except Exception:
        return False


def _require_live_transaction(session: object, transaction: object) -> None:
    if not _transaction_is_live(session, transaction):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_TERMINAL_STATE")


def _is_external_terminal_violation(exc: BaseException) -> bool:
    """A caller ended/mutated the root outside the restricted facade."""

    return (
        type(exc)
        is PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError
        and exc.code
        in {
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_TERMINAL_STATE",
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_TRANSACTION_INVALID",
        }
    )


def _external_terminal_outcome_unknown(
    *,
    exc: BaseException,
    reconciliation_identity: transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity,
) -> PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError:
    return PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
        "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_EXTERNAL_TRANSACTION_OUTCOME_UNKNOWN_HARD_FENCE",
        outcome="unknown",
        requires_hard_fence=True,
        reconciliation_identity=reconciliation_identity,
    )


def _new_transaction(session: object) -> object:
    begin = getattr(session, "begin", None)
    if not callable(begin):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
    try:
        result = begin()
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_TRANSACTION_BEGIN_FAILED"
        ) from exc
    if not inspect.isawaitable(result):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")
    _require_transaction_shape(result)
    return result


async def _start_transaction(transaction: object) -> object:
    try:
        return await transaction
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_TRANSACTION_BEGIN_FAILED"
        ) from exc


async def _terminal_transaction_call(transaction: object, *, action: str) -> None:
    callback = getattr(transaction, action, None)
    if not callable(callback):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")
    try:
        result = callback()
        if not inspect.isawaitable(result):
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")
        await result
    except PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError:
        raise
    except Exception as exc:
        code = (
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_TRANSACTION_COMMIT_FAILED"
            if action == "commit"
            else "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_TRANSACTION_ROLLBACK_FAILED"
        )
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(code) from exc


async def _rollback_if_live(
    *,
    session: object,
    transaction: object,
    reconciliation_identity: transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundReconciliationIdentity
    | None,
    hard_fence: bool,
) -> None:
    if not _transaction_is_live(session, transaction):
        return
    try:
        await _terminal_transaction_call(transaction, action="rollback")
    except PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_ROLLBACK_FAILED_HARD_FENCE",
            outcome="unknown",
            requires_hard_fence=True,
            reconciliation_identity=reconciliation_identity,
        ) from exc
    if hard_fence:
        # A successful rollback does not make a failed/unknown peer-visible
        # commit acknowledgment safe to retry.  The caller still receives the
        # original hard-fence outcome after this cleanup.
        return


def _statement_contains_textual_sql(statement: object) -> bool:
    try:
        return any(isinstance(node, TextClause) for node in iterate(statement))
    except Exception:
        return True


_CONTROL_PLANE_TABLES = (
    OperationalWriterAdmissionHead.__table__,
    OperationalWriterAdmissionCommit.__table__,
    PhysicalWalV2WitnessRoundtripStrictWriterCommit.__table__,
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit.__table__,
    PhysicalWalV2WitnessRoundtripAttestationConsumption.__table__,
)
_CONTROL_PLANE_ENTITIES = (
    OperationalWriterAdmissionHead,
    OperationalWriterAdmissionCommit,
    PhysicalWalV2WitnessRoundtripStrictWriterCommit,
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
    PhysicalWalV2WitnessRoundtripAttestationConsumption,
)


def _is_control_plane_table(value: object) -> bool:
    return any(value is table for table in _CONTROL_PLANE_TABLES)


def _value_targets_control_plane(value: object, *, seen: set[int] | None = None) -> bool:
    if value is None:
        return False
    if seen is None:
        seen = set()
    marker = id(value)
    if marker in seen:
        return False
    seen.add(marker)
    if _is_control_plane_table(value):
        return True
    for attribute in ("table", "original", "element"):
        candidate = getattr(value, attribute, None)
        if candidate is not None and candidate is not value:
            if _value_targets_control_plane(candidate, seen=seen):
                return True
    final_froms = getattr(value, "get_final_froms", None)
    if callable(final_froms):
        try:
            sources = final_froms()
        except Exception:
            return True
        try:
            return any(_value_targets_control_plane(item, seen=seen) for item in sources)
        except Exception:
            return True
    return False


def _require_allowed_business_statement(statement: object) -> None:
    if not isinstance(statement, (Select, CompoundSelect, Insert, Update, Delete)):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_BUSINESS_STATEMENT_FORBIDDEN")
    if _statement_contains_textual_sql(statement) or _value_targets_control_plane(statement):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_BUSINESS_STATEMENT_FORBIDDEN")


def _require_non_control_plane_instance(value: object) -> None:
    if isinstance(value, _CONTROL_PLANE_ENTITIES):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_BUSINESS_CONTROL_PLANE_FORBIDDEN")


def _require_non_control_plane_entity(value: object) -> None:
    if any(value is entity for entity in _CONTROL_PLANE_ENTITIES):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_BUSINESS_CONTROL_PLANE_FORBIDDEN")
    if _value_targets_control_plane(getattr(value, "__table__", None)):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_BUSINESS_CONTROL_PLANE_FORBIDDEN")


class PhysicalWalV2WitnessRoundtripStrictWriterBoundBusinessSession:
    """Small business-only facade; no lifecycle, connection, or text-SQL API."""

    def __init__(self, *, session: AsyncSession | object, transaction: object) -> None:
        self.__session = session
        self.__transaction = transaction
        self.__open = True
        self.__committed_observation: object | None = None

    def _seal(self) -> None:
        self.__open = False

    def _mark_committed(self, observation: object) -> None:
        if self.__open:
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")
        self.__committed_observation = observation

    def _require_open(self) -> None:
        if self.__open is not True:
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_BUSINESS_FACADE_CLOSED")
        _require_live_transaction(self.__session, self.__transaction)

    def verified_observation_after_known_commit(self) -> object:
        """Return the opaque response only after the owner got commit success."""

        if self.__committed_observation is None:
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_OBSERVATION_UNAVAILABLE")
        return self.__committed_observation

    async def _await_session_method(self, method: str, *args: object, **kwargs: object) -> Any:
        self._require_open()
        callback = getattr(self.__session, method, None)
        if not callable(callback):
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
        try:
            result = callback(*args, **kwargs)
            if not inspect.isawaitable(result):
                _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
            return await result
        finally:
            _require_live_transaction(self.__session, self.__transaction)

    def add(self, instance: object) -> None:
        self._require_open()
        _require_non_control_plane_instance(instance)
        callback = getattr(self.__session, "add", None)
        if not callable(callback):
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
        result = callback(instance)
        if inspect.isawaitable(result):
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
        _require_live_transaction(self.__session, self.__transaction)

    def add_all(self, instances: list[object] | tuple[object, ...]) -> None:
        self._require_open()
        if not isinstance(instances, (list, tuple)):
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_BUSINESS_ARGUMENT_INVALID")
        for instance in instances:
            _require_non_control_plane_instance(instance)
        callback = getattr(self.__session, "add_all", None)
        if not callable(callback):
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
        result = callback(instances)
        if inspect.isawaitable(result):
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_SESSION_INVALID")
        _require_live_transaction(self.__session, self.__transaction)

    async def delete(self, instance: object) -> None:
        _require_non_control_plane_instance(instance)
        await self._await_session_method("delete", instance)

    async def flush(self) -> None:
        await self._await_session_method("flush")

    async def execute(self, statement: object, parameters: object | None = None) -> Any:
        _require_allowed_business_statement(statement)
        if parameters is None:
            return await self._await_session_method("execute", statement)
        return await self._await_session_method("execute", statement, parameters)

    async def scalar(self, statement: object, parameters: object | None = None) -> Any:
        _require_allowed_business_statement(statement)
        if parameters is None:
            return await self._await_session_method("scalar", statement)
        return await self._await_session_method("scalar", statement, parameters)

    async def scalars(self, statement: object, parameters: object | None = None) -> Any:
        _require_allowed_business_statement(statement)
        if parameters is None:
            return await self._await_session_method("scalars", statement)
        return await self._await_session_method("scalars", statement, parameters)

    async def get(self, entity: object, ident: object) -> Any:
        _require_non_control_plane_entity(entity)
        return await self._await_session_method("get", entity, ident)

    async def refresh(self, instance: object) -> None:
        _require_non_control_plane_instance(instance)
        await self._await_session_method("refresh", instance)


async def _persist_before_business_dml(
    *,
    adapter: object,
    session: AsyncSession | object,
    issued_bridge: object,
    issuer: transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundOpaqueBridgeIssuer | object,
) -> transaction_adapter.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit:
    callback = getattr(adapter, "persist_bound_writer_response", None)
    if not callable(callback):
        _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_ADAPTER_INVALID")
    try:
        result = callback(
            session=session,
            issued_bridge=issued_bridge,
            issuer=issuer,
        )
        if not inspect.isawaitable(result):
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_ADAPTER_INVALID")
        persisted = await result
    except PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError:
        raise
    except transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_ADAPTER_PERSIST_FAILED",
            outcome=exc.outcome,
            requires_hard_fence=exc.requires_hard_fence,
            reconciliation_identity=exc.reconciliation_identity,
        ) from exc
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_ADAPTER_PERSIST_FAILED",
            outcome="unknown",
            requires_hard_fence=True,
        ) from exc
    if type(persisted) is transaction_adapter.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit:
        return persisted
    if (
        type(persisted)
        is transaction_adapter.DurablePhysicalWalV2WitnessRoundtripStrictWriterBoundCommitReconciliationRequired
    ):
        _fail(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_RECONCILIATION_REQUIRED_HARD_FENCE",
            outcome="known_durable",
            requires_hard_fence=True,
            reconciliation_identity=persisted.reconciliation_identity,
        )
    _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_ADAPTER_INVALID")


def _finalize_after_known_commit(
    *,
    pending: transaction_adapter.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
    config: transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionConfig,
) -> object:
    try:
        observation = transaction_adapter.finalize_pending_physical_wal_v2_witness_roundtrip_strict_writer_bound_commit(
            pending,
            config=config,
        )
    except transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionError as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_POST_COMMIT_FINALIZATION_FAILED_HARD_FENCE",
            outcome="known_durable",
            requires_hard_fence=True,
            reconciliation_identity=pending.reconciliation_identity,
        ) from exc
    except Exception as exc:
        raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_POST_COMMIT_FINALIZATION_FAILED_HARD_FENCE",
            outcome="known_durable",
            requires_hard_fence=True,
            reconciliation_identity=pending.reconciliation_identity,
        ) from exc
    if observation is None:
        _fail(
            "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_POST_COMMIT_FINALIZATION_FAILED_HARD_FENCE",
            outcome="known_durable",
            requires_hard_fence=True,
            reconciliation_identity=pending.reconciliation_identity,
        )
    return observation


class PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelope:
    """Own exactly one fresh root transaction around the Gen2 adapter."""

    def __init__(
        self,
        config: PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeConfig,
    ) -> None:
        self._config = config

    @asynccontextmanager
    async def transaction(
        self,
        *,
        session: AsyncSession | object,
        issued_bridge: object,
        issuer: transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundOpaqueBridgeIssuer | object,
    ) -> AsyncIterator[PhysicalWalV2WitnessRoundtripStrictWriterBoundBusinessSession]:
        """Yield business DML only after a flushed pending Gen2 boundary.

        The caller must create ``issued_bridge`` before entering this context.
        The issuer is forwarded only to the adapter's narrow synchronous
        capability interface; this envelope never invokes a generic callback,
        remote transport, signer, or HSM while PostgreSQL is open.
        """

        config = _require_config(self._config)
        if issued_bridge is None:
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_ISSUED_BRIDGE_REQUIRED")
        if issuer is None:
            _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_OPAQUE_ISSUER_REQUIRED")
        info = _require_fresh_session(session)
        marker = _reserve_session(info)
        adapter = transaction_adapter.PhysicalWalV2WitnessRoundtripStrictWriterBoundSqlAlchemyTransactionAdapter(
            config.sqlalchemy_transaction_config
        )
        transaction: object | None = None
        pending: transaction_adapter.PendingPhysicalWalV2WitnessRoundtripStrictWriterBoundCommit | None = None
        facade: PhysicalWalV2WitnessRoundtripStrictWriterBoundBusinessSession | None = None
        envelope_guard_lease: (
            application_envelope_guard.ApplicationWriterTransactionEnvelopeGuardLease | None
        ) = None

        try:
            # No issuer work is performed before/after this check: it must
            # already be a pre-issued opaque capability.
            _require_reserved_session_still_fresh(session, marker=marker)
            try:
                transaction = _new_transaction(session)
                started = await _start_transaction(transaction)
                if started is not transaction:
                    _fail("V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")
                _require_transaction_shape(transaction)
                _require_live_transaction(session, transaction)
                envelope_guard_lease = (
                    await application_envelope_guard.open_application_writer_transaction_envelope_guard(
                        session,
                        envelope_kind=(
                            application_envelope_guard.APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_GEN2
                        ),
                    )
                )
            except application_envelope_guard.ApplicationWriterTransactionEnvelopeGuardError as exc:
                if transaction is not None:
                    await _rollback_if_live(
                        session=session,
                        transaction=transaction,
                        reconciliation_identity=None,
                        hard_fence=False,
                    )
                raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
                    "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_APPLICATION_GUARD_REGISTRATION_FAILED"
                ) from exc
            except BaseException:
                if transaction is not None:
                    await _rollback_if_live(
                        session=session,
                        transaction=transaction,
                        reconciliation_identity=None,
                        hard_fence=False,
                    )
                raise

            try:
                pending = await _persist_before_business_dml(
                    adapter=adapter,
                    session=session,
                    issued_bridge=issued_bridge,
                    issuer=issuer,
                )
            except PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError as exc:
                await _rollback_if_live(
                    session=session,
                    transaction=transaction,
                    reconciliation_identity=exc.reconciliation_identity,
                    hard_fence=exc.requires_hard_fence,
                )
                raise
            _require_live_transaction(session, transaction)

            facade = PhysicalWalV2WitnessRoundtripStrictWriterBoundBusinessSession(
                session=session,
                transaction=transaction,
            )
            try:
                yield facade
            except BaseException as exc:
                facade._seal()
                await _rollback_if_live(
                    session=session,
                    transaction=transaction,
                    reconciliation_identity=pending.reconciliation_identity,
                    hard_fence=False,
                )
                if _is_external_terminal_violation(exc):
                    raise _external_terminal_outcome_unknown(
                        exc=exc,
                        reconciliation_identity=pending.reconciliation_identity,
                    ) from exc
                raise
            facade._seal()
            try:
                _require_live_transaction(session, transaction)
            except BaseException as exc:
                if _is_external_terminal_violation(exc):
                    raise _external_terminal_outcome_unknown(
                        exc=exc,
                        reconciliation_identity=pending.reconciliation_identity,
                    ) from exc
                raise

            try:
                await _terminal_transaction_call(transaction, action="commit")
            except PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError as exc:
                # A driver error from COMMIT can mean the server committed
                # while the client lost the response.  Rollback only as best
                # effort and expose no response/row/bridge capability.
                try:
                    await _rollback_if_live(
                        session=session,
                        transaction=transaction,
                        reconciliation_identity=pending.reconciliation_identity,
                        hard_fence=True,
                    )
                except PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError:
                    raise
                raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
                    "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_COMMIT_OUTCOME_UNKNOWN_HARD_FENCE",
                    outcome="unknown",
                    requires_hard_fence=True,
                    reconciliation_identity=pending.reconciliation_identity,
                ) from exc

            # Commit returned normally.  This is the only point at which a
            # response verifier may see the pending opaque bound capability.
            observation = _finalize_after_known_commit(
                pending=pending,
                config=config.sqlalchemy_transaction_config,
            )
            facade._mark_committed(observation)
        except BaseException:
            if facade is not None:
                facade._seal()
            raise
        finally:
            if envelope_guard_lease is not None:
                try:
                    await application_envelope_guard.close_application_writer_transaction_envelope_guard(
                        envelope_guard_lease
                    )
                except application_envelope_guard.ApplicationWriterTransactionEnvelopeGuardError as exc:
                    raise PhysicalWalV2WitnessRoundtripStrictWriterBoundTransactionEnvelopeError(
                        "V2_WITNESS_STRICT_WRITER_BOUND_TRANSACTION_ENVELOPE_APPLICATION_GUARD_RELEASE_FAILED"
                    ) from exc
