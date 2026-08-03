"""Fail-closed PostgreSQL 2PC boundary for the FI durability journal.

The regular DR delivery worker is intentionally asynchronous.  This module is
the much narrower path used only when the explicitly enabled WebApp-FI
same-region journal is coordinating a *critical* database commit:

    opaque remote PREPARE -> PostgreSQL PREPARE TRANSACTION
    -> remote COMMIT decision -> PostgreSQL COMMIT PREPARED

If phase two cannot be observed, the local prepared transaction is preserved
for the recovery runbook.  It is never silently rolled back after a possibly
committed remote decision.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from core.config import settings
from core.dr_durability_journal_client import (
    DurabilityJournalClientError,
    PreparedJournalTransaction,
    commit_prepared_journal,
    prepare_session_journal,
    rollback_prepared_journal,
)
from core.dr_event_outbox import current_dr_transaction_event_ids
from core.runtime_identity import resolve_runtime_identity
from core.runtime_sites import SITE_WEBAPP_FI


class DurabilityJournalInDoubtError(RuntimeError):
    """A local prepared transaction was intentionally left for recovery."""


_JOURNAL_TRANSACTION_KEY = "_dr_same_region_journal_transaction"
_IN_DOUBT_KEY = "_dr_same_region_journal_in_doubt"
_REGISTERED = False


def same_region_two_phase_enabled() -> bool:
    """Return whether this process must use the reviewed 2PC coordinator."""

    return bool(
        settings.dr_same_region_journal_enabled
        and settings.dr_same_region_journal_two_phase_enabled
    )


def _assert_runtime_source() -> None:
    identity = resolve_runtime_identity(settings)
    if identity.physical_site != SITE_WEBAPP_FI or not identity.is_webapp_authority:
        raise RuntimeError("same-region journal 2PC is permitted only on the WebApp-FI authority")


def _prepared(session: Session) -> PreparedJournalTransaction | None:
    value = session.info.get(_JOURNAL_TRANSACTION_KEY)
    if value is None:
        return None
    if not isinstance(value, PreparedJournalTransaction):
        raise RuntimeError("same-region journal session state is invalid")
    return value


def _prepare_remote_journal_before_local_prepare(session: Session) -> None:
    """Run after DR envelope finalization and before PostgreSQL PREPARE."""

    if not same_region_two_phase_enabled() or session.in_nested_transaction():
        return
    if session.info.get(_JOURNAL_TRANSACTION_KEY) is not None:
        return
    transaction = prepare_session_journal(session, current_dr_transaction_event_ids(session))
    if transaction is not None:
        session.info[_JOURNAL_TRANSACTION_KEY] = transaction


def _clear_journal_state(session: Session) -> None:
    if session.in_nested_transaction() or session.info.get(_IN_DOUBT_KEY):
        return
    session.info.pop(_JOURNAL_TRANSACTION_KEY, None)


def register_same_region_two_phase_listener() -> None:
    """Register after the outbox finalizer so it sees immutable envelopes."""

    global _REGISTERED
    if _REGISTERED:
        return
    event.listen(Session, "before_commit", _prepare_remote_journal_before_local_prepare)
    event.listen(Session, "after_commit", _clear_journal_state)
    event.listen(Session, "after_rollback", _clear_journal_state)
    _REGISTERED = True


class DurabilityCoordinatedSession(Session):
    """Session which inserts the remote decision between PostgreSQL 2PC phases."""

    def __init__(self, **kwargs: Any) -> None:
        coordinated = same_region_two_phase_enabled()
        if coordinated:
            _assert_runtime_source()
            if "twophase" in kwargs and not kwargs["twophase"]:
                raise RuntimeError("same-region journal cannot disable PostgreSQL two-phase commit")
            kwargs["twophase"] = True
        super().__init__(**kwargs)

    def _preserve_prepared_transaction(self, transaction: PreparedJournalTransaction) -> None:
        """Ensure normal request cleanup cannot issue ROLLBACK PREPARED.

        SQLAlchemy owns the connection lifecycle.  Its default close path
        rolls back an active 2PC handle, which is unsafe after phase-two
        transport becomes uncertain.  Marking the per-session handles as
        non-owning lets the connection close while PostgreSQL retains the
        prepared transaction for the explicit reconciliation command.
        """

        state = self._transaction
        if state is None:
            raise RuntimeError("local prepared transaction state disappeared")
        self.info[_IN_DOUBT_KEY] = transaction
        for bind, value in list(state._connections.items()):  # noqa: SLF001 - SQLAlchemy cleanup guard
            connection, db_transaction, _should_commit, autoclose = value
            # ``Connection.close()`` otherwise calls ``transaction.close()``,
            # which would emit ROLLBACK PREPARED.  PostgreSQL has already
            # detached a prepared transaction from this DB session, so after
            # de-association a normal connection-pool reset is harmless and
            # the durable prepared transaction remains available to recovery.
            if getattr(connection, "_transaction", None) is db_transaction:  # noqa: SLF001
                db_transaction._deactivate_from_connection()  # noqa: SLF001
                connection._transaction = None  # noqa: SLF001
            state._connections[bind] = (connection, db_transaction, False, autoclose)  # noqa: SLF001

    def commit(self) -> None:
        if self.info.get(_IN_DOUBT_KEY) is not None:
            raise DurabilityJournalInDoubtError(
                "same-region journal transaction requires reconciliation before reuse"
            )
        if not self.twophase:
            super().commit()
            return
        try:
            # This dispatches all normal before_commit listeners (including
            # outbox sealing and remote opaque PREPARE) and then asks
            # PostgreSQL to PREPARE TRANSACTION.
            self.prepare()
        except Exception:
            journal = _prepared(self)
            if journal is not None:
                try:
                    rollback_prepared_journal(journal)
                except Exception as rollback_error:
                    raise DurabilityJournalInDoubtError(
                        "journal prepare failed locally and remote rollback is unverified"
                    ) from rollback_error
            super().rollback()
            raise

        journal = _prepared(self)
        if journal is not None:
            gid = journal.prepare.local_transaction_gid
            try:
                commit_prepared_journal(journal, prepared_transaction_gid=gid)
            except DurabilityJournalClientError as exc:
                self._preserve_prepared_transaction(journal)
                raise DurabilityJournalInDoubtError(
                    "remote journal commit is unverified; PostgreSQL transaction is retained for recovery"
                ) from exc
        # The SessionTransaction is already PREPARED, so this emits only
        # COMMIT PREPARED and normal SQLAlchemy after_commit cleanup.  A lost
        # database response is also in-doubt: Bot has a durable commit
        # decision, while PostgreSQL may or may not have applied it.
        try:
            super().commit()
        except Exception as exc:
            if journal is not None:
                self._preserve_prepared_transaction(journal)
                raise DurabilityJournalInDoubtError(
                    "local COMMIT PREPARED is unverified; transaction is retained for recovery"
                ) from exc
            raise

    def rollback(self) -> None:
        if self.info.get(_IN_DOUBT_KEY) is not None:
            # Deliberately do not resolve a prepared transaction based on a
            # request-level exception; the journal reconciliation command owns
            # that terminal decision.
            self.close()
            return
        super().rollback()
