"""Default-off, explicit DB-only envelope for one V1 writer transaction.

This module is intentionally a *foundation*, not an application integration.
It does not import application settings, create an engine/session, contact a
Witness, perform a migration, start a worker, or cause an external effect.
The caller supplies one new caller-local ``AsyncSession`` and a narrow,
injected issuer which mints a fresh V1 ``transaction_commit`` admission.

The envelope owns exactly one root transaction on that session:

* it refuses a session with an active/prior transaction or pending ORM state;
* it obtains one freshly injected transaction-commit admission *before* it
  opens PostgreSQL work, so Witness/relay I/O is never held inside the DB
  transaction;
* it then begins the transaction itself and persists that admission through
  the reviewed PostgreSQL V1 admission adapter before yielding any business-DML
  capability;
* it yields only a deliberately small, no-commit/no-rollback facade; and
* it commits once on ordinary exit or rolls back once on a failed attempt.

It deliberately does *not* make arbitrary external effects atomic.  An
external-effect V1 admission is rejected here and must use its own stricter
boundary.  The original session remains caller-owned for lifecycle purposes;
this envelope never closes it, but marks it consumed so it cannot be reused by
another envelope attempt.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import inspect
from threading import RLock
from typing import Any, Protocol
from weakref import WeakSet

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import TextClause
from sqlalchemy.sql.selectable import CompoundSelect, Select
from sqlalchemy.sql.visitors import iterate

from core import application_writer_transaction_envelope_guard as application_envelope_guard
from core import physical_operational_failover_v1_writer_admission as admission
from core import physical_operational_failover_v1_writer_admission_sqlalchemy_transaction as sqlalchemy_admission
from models.operational_writer_admission import (
    OperationalWriterAdmissionCommit,
    OperationalWriterAdmissionHead,
)


__all__ = (
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_CONTRACT",
    "PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_DEFAULT_ENABLED",
    "PhysicalOperationalFailoverV1WriterBusinessSession",
    "PhysicalOperationalFailoverV1WriterTransactionAdmissionIssuer",
    "PhysicalOperationalFailoverV1WriterTransactionEnvelope",
    "PhysicalOperationalFailoverV1WriterTransactionEnvelopeConfig",
    "PhysicalOperationalFailoverV1WriterTransactionEnvelopeError",
)


PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_CONTRACT = (
    "gold-trade-physical-operational-failover-v1-writer-transaction-envelope-v1"
)
PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_DEFAULT_ENABLED = False

# Deliberately private object keys prevent ordinary application ``info`` keys
# from colliding with this one.  The consumed marker is intentionally retained
# after a commit or rollback: a caller must allocate another local session for
# a new admitted writer transaction.
_CONSUMED_SESSION_INFO_KEY = object()


class PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(RuntimeError):
    """One V1 writer transaction could not preserve its fail-closed contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(code)


@dataclass(frozen=True)
class PhysicalOperationalFailoverV1WriterTransactionEnvelopeConfig:
    """Explicit default-off switch with no DSN, route, or runtime wiring."""

    enabled: bool = PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_DEFAULT_ENABLED


class PhysicalOperationalFailoverV1WriterTransactionAdmissionIssuer(Protocol):
    """Root-owned future boundary which mints one fresh transaction admission.

    The issuer receives neither the business session nor an externally supplied
    admission.  Its implementation must obtain/revalidate V1 state and return
    a newly minted ``transaction_commit`` admission for this one attempt.
    This module validates the opaque V1 capability and consumes it locally, but
    it intentionally implements no Witness/runtime transport itself.
    """

    async def issue_transaction_commit_admission(
        self,
    ) -> admission.PhysicalOperationalFailoverV1WriterAdmission: ...


def _require_enabled(
    config: object,
) -> PhysicalOperationalFailoverV1WriterTransactionEnvelopeConfig:
    if type(config) is not PhysicalOperationalFailoverV1WriterTransactionEnvelopeConfig:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_CONFIG_INVALID")
    if config.enabled is False:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_DISABLED")
    if config.enabled is not True:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_CONFIG_INVALID")
    return config


def _session_info(session: object) -> dict[object, object]:
    info = getattr(session, "info", None)
    if not isinstance(info, dict):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
    return info


def _session_in_transaction(session: object) -> bool:
    checker = getattr(session, "in_transaction", None)
    if not callable(checker):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
    try:
        value = checker()
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(
            "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID"
        ) from exc
    if type(value) is not bool:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
    return value


def _session_is_healthy(session: object) -> bool:
    value = getattr(session, "is_active", None)
    if type(value) is not bool:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
    return value


def _require_empty_session_state(session: object) -> None:
    for attribute in ("new", "dirty", "deleted", "identity_map"):
        value = getattr(session, attribute, None)
        if value is None:
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
        try:
            if bool(value):
                _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_PENDING_MUTATION")
        except PhysicalOperationalFailoverV1WriterTransactionEnvelopeError:
            raise
        except Exception as exc:
            raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(
                "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID"
            ) from exc


def _require_postgresql(session: object) -> None:
    get_bind = getattr(session, "get_bind", None)
    if not callable(get_bind):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
    try:
        bind = get_bind()
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(
            "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID"
        ) from exc
    dialect = getattr(bind, "dialect", None)
    if getattr(dialect, "name", None) != "postgresql":
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_POSTGRES_REQUIRED")


def _require_fresh_session(session: object) -> dict[object, object]:
    info = _session_info(session)
    if _CONSUMED_SESSION_INFO_KEY in info:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_REUSED")
    if _session_in_transaction(session):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_NOT_FRESH")
    if _session_is_healthy(session) is not True:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_TERMINAL_STATE")
    _require_empty_session_state(session)
    _require_postgresql(session)
    return info


def _reserve_session(info: dict[object, object]) -> object:
    # Retain a unique marker rather than clearing it in ``finally``.  A finished
    # AsyncSession could otherwise silently be reused for another V1 attempt.
    marker = object()
    info[_CONSUMED_SESSION_INFO_KEY] = marker
    return marker


def _require_reserved_session_still_fresh(session: object, *, marker: object) -> None:
    """Recheck only local session state after issuer I/O, before ``begin``.

    The issuer has no session capability by protocol, but this defensive
    recheck catches a caller which touched the same session while a future
    issuer performed its fresh Witness/relay work.  It intentionally performs
    no remote revalidation; the PostgreSQL adapter's local head CAS remains the
    in-transaction admission coupling point.
    """

    info = _session_info(session)
    if info.get(_CONSUMED_SESSION_INFO_KEY) is not marker:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_REUSED")
    if _session_in_transaction(session):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_NOT_FRESH")
    if _session_is_healthy(session) is not True:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_TERMINAL_STATE")
    _require_empty_session_state(session)
    _require_postgresql(session)


def _require_transaction_methods(transaction: object) -> None:
    for attribute in ("commit", "rollback"):
        if not callable(getattr(transaction, attribute, None)):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")


def _transaction_is_active(transaction: object) -> bool:
    value = getattr(transaction, "is_active", None)
    if type(value) is not bool:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")
    return value


def _require_root_transaction(transaction: object) -> None:
    nested = getattr(transaction, "nested", None)
    if nested is not False:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")


def _transaction_is_live(session: object, transaction: object) -> bool:
    try:
        return _session_in_transaction(session) and _transaction_is_active(transaction)
    except PhysicalOperationalFailoverV1WriterTransactionEnvelopeError:
        return False


def _require_live_transaction(session: object, transaction: object) -> None:
    if not _transaction_is_live(session, transaction):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_TERMINAL_STATE")


def _new_transaction(session: object) -> object:
    begin = getattr(session, "begin", None)
    if not callable(begin):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
    try:
        transaction = begin()
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(
            "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_TRANSACTION_BEGIN_FAILED"
        ) from exc
    if not inspect.isawaitable(transaction):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")
    _require_transaction_methods(transaction)
    return transaction


async def _start_transaction(transaction: object) -> object:
    try:
        return await transaction
    except PhysicalOperationalFailoverV1WriterTransactionEnvelopeError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(
            "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_TRANSACTION_BEGIN_FAILED"
        ) from exc


async def _terminal_transaction_call(transaction: object, *, action: str) -> None:
    callback = getattr(transaction, action, None)
    if not callable(callback):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")
    try:
        result = callback()
        if not inspect.isawaitable(result):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")
        await result
    except PhysicalOperationalFailoverV1WriterTransactionEnvelopeError:
        raise
    except Exception as exc:
        code = (
            "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_TRANSACTION_COMMIT_FAILED"
            if action == "commit"
            else "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_TRANSACTION_ROLLBACK_FAILED"
        )
        raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(code) from exc


async def _issue_transaction_commit_admission(
    issuer: object,
) -> admission.PhysicalOperationalFailoverV1WriterAdmission:
    callback = getattr(issuer, "issue_transaction_commit_admission", None)
    if not callable(callback):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_ISSUER_INVALID")
    try:
        value = callback()
        if not inspect.isawaitable(value):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_ISSUER_INVALID")
        result = await value
    except PhysicalOperationalFailoverV1WriterTransactionEnvelopeError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(
            "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_ISSUER_FAILED"
        ) from exc
    return _require_transaction_commit_admission(result)


def _require_transaction_commit_admission(
    value: object,
) -> admission.PhysicalOperationalFailoverV1WriterAdmission:
    if (
        type(value) is not admission.PhysicalOperationalFailoverV1WriterAdmission
        or value._capability is not admission._ADMISSION_CAPABILITY
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_INVALID")
    operation = value.operation
    if (
        type(operation) is not admission.PhysicalOperationalFailoverV1WriterOperation
        or operation._capability is not admission._OPERATION_CAPABILITY
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_INVALID")
    if operation.operation_kind == admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_EXTERNAL_EFFECT:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_EXTERNAL_EFFECT_FORBIDDEN")
    if operation.operation_kind != admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_INVALID")
    return value


def _require_reviewed_adapter(
    value: object,
) -> sqlalchemy_admission.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionAdapter:
    if (
        type(value)
        is not sqlalchemy_admission.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionAdapter
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_ADAPTER_INVALID")
    return value


async def _persist_admission_first(
    *,
    adapter: sqlalchemy_admission.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionAdapter,
    session: AsyncSession | object,
    writer_admission: admission.PhysicalOperationalFailoverV1WriterAdmission,
) -> sqlalchemy_admission.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt:
    try:
        result = adapter.persist_writer_admission(
            session=session,
            writer_admission=writer_admission,
        )
        if not inspect.isawaitable(result):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_PERSIST_FAILED")
        receipt = await result
    except PhysicalOperationalFailoverV1WriterTransactionEnvelopeError:
        raise
    except Exception as exc:
        raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(
            "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_PERSIST_FAILED"
        ) from exc
    if receipt is None:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_ADAPTER_DISABLED")
    if type(receipt) is not sqlalchemy_admission.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt:
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_PERSIST_FAILED")
    return receipt


def _statement_contains_textual_sql(statement: object) -> bool:
    try:
        return any(isinstance(node, TextClause) for node in iterate(statement))
    except Exception:
        return True


_ADMISSION_CONTROL_PLANE_TABLES = (
    OperationalWriterAdmissionHead.__table__,
    OperationalWriterAdmissionCommit.__table__,
)


def _is_admission_control_plane_table(value: object) -> bool:
    return any(value is table for table in _ADMISSION_CONTROL_PLANE_TABLES)


def _value_targets_admission_control_plane(value: object, *, seen: set[int] | None = None) -> bool:
    """Reject direct, aliased, CTE, or ORM references to V1 control-plane rows."""

    if value is None:
        return False
    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return False
    seen.add(identity)
    if _is_admission_control_plane_table(value):
        return True
    for attribute in ("table", "original", "element"):
        candidate = getattr(value, attribute, None)
        if candidate is not None and candidate is not value:
            if _value_targets_admission_control_plane(candidate, seen=seen):
                return True
    final_froms = getattr(value, "get_final_froms", None)
    if callable(final_froms):
        try:
            sources = final_froms()
        except Exception:
            # An uninspectable executable is not a safe business facade input.
            return True
        try:
            return any(
                _value_targets_admission_control_plane(source, seen=seen)
                for source in sources
            )
        except Exception:
            return True
    return False


def _statement_targets_admission_control_plane(statement: object) -> bool:
    return _value_targets_admission_control_plane(statement)


def _require_allowed_business_statement(statement: object) -> None:
    if not isinstance(statement, (Select, CompoundSelect, Insert, Update, Delete)):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_BUSINESS_STATEMENT_FORBIDDEN")
    if _statement_contains_textual_sql(statement) or _statement_targets_admission_control_plane(statement):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_BUSINESS_STATEMENT_FORBIDDEN")


def _require_non_control_plane_instance(instance: object) -> None:
    if isinstance(instance, (OperationalWriterAdmissionHead, OperationalWriterAdmissionCommit)):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_BUSINESS_CONTROL_PLANE_FORBIDDEN")


def _require_non_control_plane_entity(entity: object) -> None:
    if any(
        entity is control_plane_entity
        for control_plane_entity in (OperationalWriterAdmissionHead, OperationalWriterAdmissionCommit)
    ):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_BUSINESS_CONTROL_PLANE_FORBIDDEN")
    if _value_targets_admission_control_plane(getattr(entity, "__table__", None)):
        _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_BUSINESS_CONTROL_PLANE_FORBIDDEN")


class PhysicalOperationalFailoverV1WriterBusinessSession:
    """Small, transaction-bound business DML facade with no terminal methods.

    The facade intentionally omits ``commit``, ``rollback``, ``close``,
    ``begin``, connection access, callbacks, and arbitrary textual SQL.  It is
    valid only while the envelope owns the root transaction.  Its methods are
    deliberately enough for a future explicit migration, not a replacement
    for the complete ``AsyncSession`` API.
    """

    def __init__(self, *, session: AsyncSession | object, transaction: object) -> None:
        self.__session = session
        self.__transaction = transaction
        self.__open = True

    def _seal(self) -> None:
        self.__open = False

    def _require_open(self) -> None:
        if self.__open is not True:
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_BUSINESS_FACADE_CLOSED")
        _require_live_transaction(self.__session, self.__transaction)

    async def _await_session_method(
        self,
        method_name: str,
        *args: object,
        **kwargs: object,
    ) -> Any:
        self._require_open()
        callback = getattr(self.__session, method_name, None)
        if not callable(callback):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
        try:
            result = callback(*args, **kwargs)
            if not inspect.isawaitable(result):
                _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
            value = await result
        finally:
            # A direct caller-side terminal operation must be detected before
            # any further facade method or envelope commit can proceed.
            _require_live_transaction(self.__session, self.__transaction)
        return value

    def add(self, instance: object) -> None:
        self._require_open()
        _require_non_control_plane_instance(instance)
        callback = getattr(self.__session, "add", None)
        if not callable(callback):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
        try:
            result = callback(instance)
        except PhysicalOperationalFailoverV1WriterTransactionEnvelopeError:
            raise
        except Exception:
            raise
        if inspect.isawaitable(result):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
        _require_live_transaction(self.__session, self.__transaction)

    def add_all(self, instances: list[object] | tuple[object, ...]) -> None:
        self._require_open()
        if not isinstance(instances, (list, tuple)):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_BUSINESS_ARGUMENT_INVALID")
        for instance in instances:
            _require_non_control_plane_instance(instance)
        callback = getattr(self.__session, "add_all", None)
        if not callable(callback):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
        try:
            result = callback(instances)
        except Exception:
            raise
        if inspect.isawaitable(result):
            _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_INVALID")
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


class PhysicalOperationalFailoverV1WriterTransactionEnvelope:
    """Own one explicit V1-admitted transaction at a time on a fresh session."""

    def __init__(
        self,
        config: PhysicalOperationalFailoverV1WriterTransactionEnvelopeConfig,
        *,
        admission_issuer: PhysicalOperationalFailoverV1WriterTransactionAdmissionIssuer | object | None = None,
        admission_adapter: sqlalchemy_admission.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionAdapter
        | object
        | None = None,
    ) -> None:
        self._config = config
        self._admission_issuer = admission_issuer
        self._admission_adapter = admission_adapter
        # This registry is per envelope service.  It rejects a provider which
        # hands back the same admission object/equivalent capability on a retry
        # while allowing independent envelope services in isolated processes.
        self._used_admissions: WeakSet[admission.PhysicalOperationalFailoverV1WriterAdmission] = WeakSet()
        self._used_admissions_lock = RLock()

    def _claim_fresh_admission(
        self,
        writer_admission: admission.PhysicalOperationalFailoverV1WriterAdmission,
    ) -> None:
        try:
            with self._used_admissions_lock:
                if writer_admission in self._used_admissions:
                    _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_REUSED")
                self._used_admissions.add(writer_admission)
        except PhysicalOperationalFailoverV1WriterTransactionEnvelopeError:
            raise
        except Exception as exc:
            raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(
                "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_ADMISSION_INVALID"
            ) from exc

    @asynccontextmanager
    async def transaction(
        self,
        *,
        session: AsyncSession | object,
    ) -> AsyncIterator[PhysicalOperationalFailoverV1WriterBusinessSession]:
        """Run one fresh V1-admitted business transaction.

        A direct caller with the original ``session`` can still misuse it; the
        facade does not expose terminal methods and the envelope detects a
        caller-side commit/rollback before its own terminal action.  Such a
        violation is rejected rather than being silently treated as success.
        """

        _require_enabled(self._config)
        adapter = _require_reviewed_adapter(self._admission_adapter)
        info = _require_fresh_session(session)
        marker = _reserve_session(info)

        transaction: object | None = None
        facade: PhysicalOperationalFailoverV1WriterBusinessSession | None = None
        envelope_guard_lease: (
            application_envelope_guard.ApplicationWriterTransactionEnvelopeGuardLease | None
        ) = None
        commit_attempted = False
        committed = False
        try:
            # The issuer intentionally runs outside a database transaction:
            # obtaining fresh Witness/relay evidence must never hold a PostgreSQL
            # transaction or V1 head lock across remote I/O.
            writer_admission = await _issue_transaction_commit_admission(self._admission_issuer)
            self._claim_fresh_admission(writer_admission)
            _require_reserved_session_still_fresh(session, marker=marker)

            transaction = _new_transaction(session)
            started = await _start_transaction(transaction)
            if started is not transaction:
                _fail("OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_TRANSACTION_INVALID")
            _require_root_transaction(transaction)
            _require_live_transaction(session, transaction)
            try:
                envelope_guard_lease = (
                    await application_envelope_guard.open_application_writer_transaction_envelope_guard(
                        session,
                        envelope_kind=(
                            application_envelope_guard.APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_V1
                        ),
                    )
                )
            except application_envelope_guard.ApplicationWriterTransactionEnvelopeGuardError as exc:
                raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(
                    "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_APPLICATION_GUARD_REGISTRATION_FAILED"
                ) from exc

            await _persist_admission_first(
                adapter=adapter,
                session=session,
                writer_admission=writer_admission,
            )
            _require_live_transaction(session, transaction)

            facade = PhysicalOperationalFailoverV1WriterBusinessSession(
                session=session,
                transaction=transaction,
            )
            try:
                yield facade
            finally:
                facade._seal()

            _require_live_transaction(session, transaction)
            commit_attempted = True
            await _terminal_transaction_call(transaction, action="commit")
            committed = True
        except BaseException as exc:
            if facade is not None:
                facade._seal()
            if committed:
                raise
            if transaction is None:
                raise

            # If the root transaction was already ended outside the facade,
            # never pretend a second rollback repairs it.  A failed envelope
            # commit is distinct: rollback once if the driver still reports an
            # active transaction, then preserve the commit failure.
            live = _transaction_is_live(session, transaction)
            if not live:
                if commit_attempted:
                    raise
                if isinstance(exc, PhysicalOperationalFailoverV1WriterTransactionEnvelopeError):
                    if exc.code == "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_TERMINAL_STATE":
                        raise
                raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(
                    "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_SESSION_TERMINAL_STATE"
                ) from exc

            try:
                await _terminal_transaction_call(transaction, action="rollback")
            except BaseException:
                # A rollback failure is itself terminal for this caller-local
                # session.  Do not attempt a second terminal operation.
                raise
            raise
        finally:
            if envelope_guard_lease is not None:
                try:
                    await application_envelope_guard.close_application_writer_transaction_envelope_guard(
                        envelope_guard_lease
                    )
                except application_envelope_guard.ApplicationWriterTransactionEnvelopeGuardError as exc:
                    raise PhysicalOperationalFailoverV1WriterTransactionEnvelopeError(
                        "OPERATIONAL_FAILOVER_V1_WRITER_TRANSACTION_ENVELOPE_APPLICATION_GUARD_RELEASE_FAILED"
                    ) from exc
