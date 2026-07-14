"""Transaction-bound fencing for WebApp-authoritative mutations."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterator

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from core.runtime_identity import RuntimeIdentity
from core.config import settings
from core.webapp_writer_control import WriterStateSnapshot, snapshot_is_local_active


class WriterFenceError(RuntimeError):
    """Raised when a WebApp-authoritative transaction has lost writer ownership."""


@dataclass(frozen=True)
class WriterFenceContext:
    physical_site: str
    writer_epoch: int
    transition_id: str
    witness_lease_id: str | None
    require_witness_lease: bool
    source: str


_writer_fence_context: ContextVar[WriterFenceContext | None] = ContextVar(
    "webapp_writer_fence_context",
    default=None,
)
_listener_registered = False


def current_writer_fence_context() -> WriterFenceContext | None:
    return _writer_fence_context.get()


@contextmanager
def writer_fence_scope(
    identity: RuntimeIdentity,
    snapshot: WriterStateSnapshot,
    *,
    source: str,
    require_witness_lease: bool = False,
) -> Iterator[WriterFenceContext]:
    active, reasons = snapshot_is_local_active(
        identity,
        snapshot,
        require_witness_lease=require_witness_lease,
    )
    if not active:
        raise WriterFenceError("writer preflight rejected: " + ",".join(reasons))
    context = WriterFenceContext(
        physical_site=identity.physical_site,
        writer_epoch=snapshot.writer_epoch,
        transition_id=snapshot.transition_id,
        witness_lease_id=snapshot.witness_lease_id,
        require_witness_lease=require_witness_lease,
        source=source,
    )
    token: Token = _writer_fence_context.set(context)
    try:
        yield context
    finally:
        _writer_fence_context.reset(token)


def _enforce_writer_fence_before_commit(session: Session) -> None:
    context = current_writer_fence_context()
    if context is None:
        return
    row = session.connection().execute(
        text(
            """
            SELECT active_site, writer_epoch, control_state, transition_id,
                   witness_lease_id, witness_lease_expires_at,
                   clock_timestamp() AS database_now
            FROM webapp_writer_state
            WHERE authority = 'webapp'
            FOR SHARE
            """
        )
    ).mappings().one_or_none()
    if row is None:
        raise WriterFenceError("writer state is missing at commit boundary")
    if row["control_state"] != "active":
        raise WriterFenceError("writer is fenced at commit boundary")
    if row["active_site"] != context.physical_site:
        raise WriterFenceError("active physical site changed before commit")
    if int(row["writer_epoch"]) != context.writer_epoch:
        raise WriterFenceError("writer epoch changed before commit")
    if row["transition_id"] != context.transition_id:
        raise WriterFenceError("writer transition changed before commit")
    if context.require_witness_lease:
        if not context.witness_lease_id or row["witness_lease_id"] != context.witness_lease_id:
            raise WriterFenceError("writer witness lease changed before commit")
        if row["witness_lease_expires_at"] is None:
            raise WriterFenceError("writer witness lease expiry is missing at commit boundary")
        safety_deadline = row["database_now"] + timedelta(
            seconds=max(0, int(settings.writer_witness_safety_margin_seconds))
        )
        if row["witness_lease_expires_at"] <= safety_deadline:
            raise WriterFenceError("writer witness lease expired before commit")


def register_writer_fence_listener() -> None:
    global _listener_registered
    if _listener_registered:
        return
    event.listen(Session, "before_commit", _enforce_writer_fence_before_commit)
    _listener_registered = True
