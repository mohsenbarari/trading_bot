"""Process-local proof that a guarded application write is inside V1/Gen2.

This is deliberately an *integration seam*, not a new writer authority.  The
application currently has many raw ``AsyncSession`` entry points in HTTP,
Bot, and worker code, while the reviewed V1 and Gen2 envelopes require a
fresh session and expose a deliberately smaller business facade.  Implicitly
wrapping those legacy paths would not be atomic and could turn an uncertain
write into a retryable one.

Instead, a future reviewed composition may enable the policy consumed by
``core.db``.  At that point every guarded mutation must prove that its exact
session and its exact SQLAlchemy connection were opened by one of the two
reviewed envelopes.  A raw application session is refused; it is never
silently upgraded into an envelope.  The policy itself defaults to disabled
and this module imports neither application settings nor a database engine.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import inspect
from pathlib import Path
from typing import Mapping

from core.application_writer_term import (
    MAX_MAX_LEASE_DURATION_SECONDS,
    MAX_SAFETY_MARGIN_SECONDS,
    MIN_MAX_LEASE_DURATION_SECONDS,
    MIN_SAFETY_MARGIN_SECONDS,
)
from core.production_writer_lease import WEBAPP_SITES


__all__ = (
    "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_CONTRACT",
    "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_DEFAULT_ENABLED",
    "APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_GEN2",
    "APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_V1",
    "ApplicationWriterTransactionEnvelopeGuardError",
    "ApplicationWriterTransactionEnvelopeGuardPolicy",
    "ApplicationWriterTransactionEnvelopeGuardLease",
    "close_application_writer_transaction_envelope_guard",
    "open_application_writer_transaction_envelope_guard",
    "policy_from_settings",
    "require_application_writer_transaction_envelope_connection",
    "require_application_writer_transaction_envelope_session",
)


APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_CONTRACT = (
    "gold-trade-application-writer-transaction-envelope-guard-v1"
)
APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_DEFAULT_ENABLED = False
APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_V1 = "v1"
APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_GEN2 = "gen2"
_ENVELOPE_KINDS = frozenset(
    {
        APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_V1,
        APPLICATION_WRITER_TRANSACTION_ENVELOPE_KIND_GEN2,
    }
)

# A private object key deliberately cannot collide with an application
# ``session.info`` string.  ContextVar state is copied into child tasks, so
# every later check also binds the marker to the opener's exact task identity.
_SESSION_INFO_KEY = object()
_LEASE_CAPABILITY = object()


class ApplicationWriterTransactionEnvelopeGuardError(RuntimeError):
    """An enabled application mutation is not inside a reviewed envelope."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise ApplicationWriterTransactionEnvelopeGuardError(code)


@dataclass(frozen=True)
class ApplicationWriterTransactionEnvelopeGuardPolicy:
    """Default-off policy consumed by the application DB write interceptors.

    A direct construction remains useful only for isolated envelope tests and
    the reviewed V1/Gen2 internals.  The canonical application runtime must
    obtain this policy through :func:`policy_from_settings`, which refuses a
    partial or legacy two-site activation before any application DML runs.
    """

    enabled: bool = APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_DEFAULT_ENABLED


def _settings_value(settings: object, name: str) -> object:
    """Read one required enabled-runtime setting without a permissive default."""

    try:
        return getattr(settings, name)
    except AttributeError as exc:
        _fail(
            "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_SETTINGS_"
            + name.upper()
            + "_REQUIRED"
        )
        raise AssertionError("unreachable") from exc


def _require_enabled_writer_term_settings(settings: object) -> None:
    """Validate static Writer Witness settings without opening its lease file.

    The term implementation still validates ownership, freshness, and the
    signed local lease at each write boundary.  This earlier check only keeps
    an envelope-only configuration from becoming an accidental writer path.
    It intentionally performs no filesystem, network, database, or Witness
    operation.
    """

    if _settings_value(settings, "single_writer_runtime_enabled") is not True:
        _fail(
            "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_"
            "SINGLE_WRITER_RUNTIME_REQUIRED"
        )
    if _settings_value(settings, "application_writer_term_enforced") is not True:
        _fail(
            "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_"
            "WRITER_TERM_ENFORCEMENT_REQUIRED"
        )
    if _settings_value(settings, "application_writer_term_local_site") not in WEBAPP_SITES:
        _fail(
            "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_"
            "WRITER_TERM_LOCAL_SITE_INVALID"
        )

    raw_lease_file = _settings_value(settings, "application_writer_term_lease_file")
    if isinstance(raw_lease_file, Path):
        lease_file = raw_lease_file
    elif isinstance(raw_lease_file, str) and raw_lease_file.strip():
        lease_file = Path(raw_lease_file)
    else:
        _fail(
            "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_"
            "WRITER_TERM_LEASE_FILE_REQUIRED"
        )
    if not lease_file.is_absolute() or any(part in {"", ".", ".."} for part in lease_file.parts):
        _fail(
            "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_"
            "WRITER_TERM_LEASE_FILE_INVALID"
        )

    safety_margin_seconds = _settings_value(
        settings,
        "application_writer_term_safety_margin_seconds",
    )
    max_lease_duration_seconds = _settings_value(
        settings,
        "application_writer_term_max_lease_duration_seconds",
    )
    if (
        type(safety_margin_seconds) is not int
        or not MIN_SAFETY_MARGIN_SECONDS
        <= safety_margin_seconds
        <= MAX_SAFETY_MARGIN_SECONDS
    ):
        _fail(
            "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_"
            "WRITER_TERM_SAFETY_MARGIN_INVALID"
        )
    if (
        type(max_lease_duration_seconds) is not int
        or not MIN_MAX_LEASE_DURATION_SECONDS
        <= max_lease_duration_seconds
        <= MAX_MAX_LEASE_DURATION_SECONDS
        or max_lease_duration_seconds <= safety_margin_seconds
    ):
        _fail(
            "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_"
            "WRITER_TERM_MAX_LEASE_DURATION_INVALID"
        )

    # ``init_db`` must never mutate an application schema after the DML fence
    # becomes active.  Alembic and explicit maintenance tools retain their
    # separately reviewed operational control planes and are not registered
    # on this canonical application engine hook.
    if _settings_value(settings, "database_schema_bootstrap_enabled") is not False:
        _fail(
            "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_"
            "DATABASE_SCHEMA_BOOTSTRAP_MUST_BE_DISABLED"
        )


def policy_from_settings(settings: object) -> ApplicationWriterTransactionEnvelopeGuardPolicy:
    """Project the one canonical application DML fence from Settings.

    The disabled path reads only its dedicated flag and returns without
    inspecting Writer Witness paths or touching any runtime resource.  An
    enabled path is intentionally coupled to an explicit single-writer,
    Writer-Witness-term runtime and an already-managed schema.  It is still
    only a local rejection fence: it cannot grant a writer term, promote a
    site, or authorize a V1/Gen2 transaction on its own.
    """

    enabled = getattr(
        settings,
        "application_writer_transaction_envelope_guard_enforced",
        False,
    )
    if enabled is False:
        return ApplicationWriterTransactionEnvelopeGuardPolicy()
    if enabled is not True:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_SETTINGS_ENABLED_INVALID")
    _require_enabled_writer_term_settings(settings)
    return ApplicationWriterTransactionEnvelopeGuardPolicy(enabled=True)


@dataclass
class _ActiveEnvelope:
    session: object
    marker: object
    envelope_kind: str
    owner_task: object
    sync_connection: object | None = None


_ACTIVE_ENVELOPE: ContextVar[_ActiveEnvelope | None] = ContextVar(
    "application_writer_transaction_envelope_guard",
    default=None,
)


@dataclass(frozen=True, eq=False, init=False)
class ApplicationWriterTransactionEnvelopeGuardLease:
    """Opaque cleanup handle held only by the envelope owning the session."""

    _active: _ActiveEnvelope = field(repr=False, compare=False)
    _reset_token: Token[_ActiveEnvelope | None] = field(repr=False, compare=False)
    _capability: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        active: _ActiveEnvelope,
        reset_token: Token[_ActiveEnvelope | None],
        capability: object,
    ) -> None:
        if capability is not _LEASE_CAPABILITY:
            raise TypeError(
                "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_LEASE_CONSTRUCTION_FORBIDDEN"
            )
        object.__setattr__(self, "_active", active)
        object.__setattr__(self, "_reset_token", reset_token)
        object.__setattr__(self, "_capability", capability)

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError(
            "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_LEASE_SERIALIZATION_FORBIDDEN"
        )


def _policy_enabled(policy: object) -> bool:
    if type(policy) is not ApplicationWriterTransactionEnvelopeGuardPolicy:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_POLICY_INVALID")
    if policy.enabled is False:
        return False
    if policy.enabled is not True:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_POLICY_INVALID")
    return True


def _session_info(session: object) -> dict[object, object]:
    info = getattr(session, "info", None)
    if not isinstance(info, dict):
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_SESSION_INVALID")
    return info


def _session_in_transaction(session: object) -> bool:
    callback = getattr(session, "in_transaction", None)
    if not callable(callback):
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_SESSION_INVALID")
    try:
        value = callback()
    except Exception as exc:
        raise ApplicationWriterTransactionEnvelopeGuardError(
            "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_SESSION_INVALID"
        ) from exc
    if type(value) is not bool:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_SESSION_INVALID")
    return value


def _active_matches_session(active: _ActiveEnvelope, session: object) -> bool:
    if (
        active.sync_connection is None
        or _current_task_identity() is not active.owner_task
    ):
        return False
    # SQLAlchemy ``Session`` events receive ``AsyncSession.sync_session``;
    # accepting only that exact paired object (rather than any same-shaped
    # session) keeps the marker usable at ORM/Core event boundaries without
    # widening it to another session.
    if (
        active.session is not session
        and getattr(active.session, "sync_session", None) is not session
    ):
        return False
    info = getattr(session, "info", None)
    return isinstance(info, Mapping) and info.get(_SESSION_INFO_KEY) is active.marker


def _active_matches_connection(active: _ActiveEnvelope, connection: object) -> bool:
    if active.sync_connection is not connection:
        return False
    return _active_matches_session(active, active.session)


def _require_envelope_kind(value: object) -> str:
    if type(value) is not str or value not in _ENVELOPE_KINDS:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_ENVELOPE_KIND_INVALID")
    return value


def _current_task_identity() -> object | None:
    """Return the exact owner task without assuming a running event loop."""

    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


async def open_application_writer_transaction_envelope_guard(
    session: object,
    *,
    envelope_kind: str,
) -> ApplicationWriterTransactionEnvelopeGuardLease:
    """Bind one already-open root transaction to V1 or Gen2 exactly once.

    Callers must invoke this only after their envelope has successfully begun
    its fresh root transaction and before it persists control-plane evidence
    or yields business DML.  The exact sync connection is captured up front,
    so a direct engine connection in the same task cannot inherit the proof.
    """

    envelope_kind = _require_envelope_kind(envelope_kind)
    if _ACTIVE_ENVELOPE.get() is not None:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_NESTED")
    info = _session_info(session)
    if _SESSION_INFO_KEY in info:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_SESSION_ALREADY_MARKED")
    if _session_in_transaction(session) is not True:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_ROOT_TRANSACTION_REQUIRED")
    owner_task = _current_task_identity()
    if owner_task is None:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_OWNER_TASK_REQUIRED")

    active = _ActiveEnvelope(
        session=session,
        marker=object(),
        envelope_kind=envelope_kind,
        owner_task=owner_task,
    )
    reset_token = _ACTIVE_ENVELOPE.set(active)
    info[_SESSION_INFO_KEY] = active.marker
    try:
        connection_factory = getattr(session, "connection", None)
        if not callable(connection_factory):
            _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_SESSION_CONNECTION_INVALID")
        connection = connection_factory()
        if not inspect.isawaitable(connection):
            _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_SESSION_CONNECTION_INVALID")
        connection = await connection
        sync_connection = getattr(connection, "sync_connection", None)
        if sync_connection is None:
            _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_SESSION_CONNECTION_INVALID")
        active.sync_connection = sync_connection
    except BaseException:
        if info.get(_SESSION_INFO_KEY) is active.marker:
            info.pop(_SESSION_INFO_KEY, None)
        _ACTIVE_ENVELOPE.reset(reset_token)
        raise
    return ApplicationWriterTransactionEnvelopeGuardLease(
        active=active,
        reset_token=reset_token,
        capability=_LEASE_CAPABILITY,
    )


async def close_application_writer_transaction_envelope_guard(
    lease: object,
) -> None:
    """Remove an exact envelope marker after its sole terminal action."""

    if type(lease) is not ApplicationWriterTransactionEnvelopeGuardLease:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_LEASE_INVALID")
    try:
        capability = lease._capability
        active = lease._active
        reset_token = lease._reset_token
    except AttributeError:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_LEASE_INVALID")
    if capability is not _LEASE_CAPABILITY or type(active) is not _ActiveEnvelope:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_LEASE_INVALID")
    current = _ACTIVE_ENVELOPE.get()
    info = getattr(active.session, "info", None)
    valid = (
        current is active
        and _current_task_identity() is active.owner_task
        and isinstance(info, dict)
        and info.get(_SESSION_INFO_KEY) is active.marker
    )
    if not valid:
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_LEASE_INVALID")
    try:
        _ACTIVE_ENVELOPE.reset(reset_token)
    except (RuntimeError, ValueError) as exc:
        raise ApplicationWriterTransactionEnvelopeGuardError(
            "APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_LEASE_INVALID"
        ) from exc
    # ``valid`` proves this is the exact mutable mapping installed by ``open``.
    assert isinstance(info, dict)
    info.pop(_SESSION_INFO_KEY, None)


def require_application_writer_transaction_envelope_session(
    policy: ApplicationWriterTransactionEnvelopeGuardPolicy,
    session: object,
) -> None:
    """Refuse an enabled guarded ORM/Core mutation outside V1/Gen2."""

    if not _policy_enabled(policy):
        return
    active = _ACTIVE_ENVELOPE.get()
    if active is None or not _active_matches_session(active, session):
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_ENVELOPE_REQUIRED")


def require_application_writer_transaction_envelope_connection(
    policy: ApplicationWriterTransactionEnvelopeGuardPolicy,
    connection: object,
) -> None:
    """Refuse enabled direct SQL unless it uses the exact envelope connection."""

    if not _policy_enabled(policy):
        return
    active = _ACTIVE_ENVELOPE.get()
    if active is None or not _active_matches_connection(active, connection):
        _fail("APPLICATION_WRITER_TRANSACTION_ENVELOPE_GUARD_ENVELOPE_REQUIRED")
