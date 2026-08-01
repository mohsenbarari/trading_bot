"""Pure execution contract for a dedicated atomic Object-delta applier.

This module is intentionally a narrow adapter boundary, not a production
importer.  It does not import a database library, open a connection, read an
Object Storage object, authenticate a request, or call any application side
effect.  A future deployment-specific adapter supplies the one transaction
that implements this protocol after transport verification and pure import
planning have already succeeded.

The legacy sync receiver is deliberately outside this boundary.  In
particular, an implementation of ``DedicatedObjectDeltaApplyTransaction``
may mutate only the database rows represented by the supplied plan; it must
not publish realtime messages, enqueue Telegram work, make HTTP calls, or
emit cache/audit events from this transaction.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from core.object_delta_import_plan import AtomicObjectDeltaImportPlan, PlannedObjectDeltaChange
from core.object_delta_receiver_mvp_handlers import (
    ObjectDeltaReceiverMvpHandlerError,
    require_object_delta_mvp_receiver_planned_change,
)


IMPORT_ACTION_APPLY = "apply"
IMPORT_ACTION_REPLAY = "replay"
DEDICATED_OBJECT_DELTA_APPLIER_CONTRACT = "gold-trade-object-delta-db-only-applier-v1"


class DedicatedObjectDeltaAtomicApplyError(ValueError):
    """Raised when a plan or adapter cannot satisfy the atomic apply contract."""


@runtime_checkable
class DedicatedObjectDeltaApplyTransaction(Protocol):
    """One caller-owned transaction with no non-database side-effect hooks.

    The concrete implementation must use a single database transaction for
    every method from ``apply_db_change`` through ``commit``.  It must not
    commit inside any earlier method, and ``apply_db_change`` must not call
    HTTP, realtime, Telegram, cache, notification, or audit adapters.
    """

    contract_name: str

    async def apply_db_change(self, change: PlannedObjectDeltaChange) -> None:
        """Apply exactly one normalized database mutation."""

    async def insert_immutable_receipt(self, receipt: object) -> None:
        """Persist the batch receipt in the same transaction."""

    async def write_receiver_cursor(self, cursor: object) -> None:
        """Persist the receiver cursor in the same transaction."""

    async def commit(self) -> None:
        """Commit once, only after every planned write succeeds."""

    async def rollback(self) -> None:
        """Rollback the same transaction after any failed apply operation."""


@runtime_checkable
class DedicatedObjectDeltaApplyAdapter(Protocol):
    """Factory for a dedicated no-side-effect transaction.

    This protocol intentionally does not expose a generic application session
    or callbacks.  Keeping the capabilities this small prevents a future
    adapter from silently reusing side-effecting sync paths.
    """

    contract_name: str

    async def begin_atomic_object_delta_apply(self) -> DedicatedObjectDeltaApplyTransaction:
        """Return one new transaction for one apply plan."""


@dataclass(frozen=True)
class AtomicObjectDeltaApplyResult:
    """Non-secret result of applying a plan through the dedicated adapter."""

    action: str
    changes_applied: int


REQUIRED_DEDICATED_OBJECT_DELTA_APPLIER_INVARIANTS = (
    "accept only a previously validated atomic Object-delta import plan",
    "accept only opaque release-pinned receiver handler changes, never generic sync-item mappings",
    "replay plans open no transaction and perform no write",
    "an apply plan opens exactly one dedicated database transaction",
    "apply every planned db_change in ascending contiguous logical sequence",
    "insert the immutable receipt and write the receiver cursor in that same transaction",
    "a packet-authorized receiver must additionally consume its durable nonce in that same transaction; this generic helper does not yet provide a receiver runtime",
    "commit exactly once only after every planned database write succeeds",
    "rollback the same transaction after any failed apply operation",
    "do not invoke realtime, Telegram, HTTP, cache, audit, or legacy sync behaviour",
)


def _require_contract_name(value: object, *, label: str) -> None:
    if value != DEDICATED_OBJECT_DELTA_APPLIER_CONTRACT:
        raise DedicatedObjectDeltaAtomicApplyError(
            f"{label} does not declare the dedicated Object-delta applier contract"
        )


def _require_async_method(value: object, *, label: str) -> Callable[..., Awaitable[None]]:
    if not callable(value):
        raise DedicatedObjectDeltaAtomicApplyError(f"{label} is missing")
    return value


def _validate_apply_plan(
    plan: object,
) -> tuple[str, tuple[PlannedObjectDeltaChange, ...], object | None, object | None]:
    if type(plan) is not AtomicObjectDeltaImportPlan:
        raise DedicatedObjectDeltaAtomicApplyError("atomic Object-delta import plan is invalid")

    action = plan.action
    changes = plan.changes_to_apply
    receipt = plan.receipt_to_insert
    cursor = plan.cursor_to_write
    if not isinstance(changes, tuple):
        raise DedicatedObjectDeltaAtomicApplyError(
            "planned database changes must be an immutable tuple"
        )
    if action == IMPORT_ACTION_REPLAY:
        if changes or receipt is not None or cursor is not None:
            raise DedicatedObjectDeltaAtomicApplyError(
                "replay plans must contain no database writes"
            )
        return action, changes, receipt, cursor
    if action != IMPORT_ACTION_APPLY:
        raise DedicatedObjectDeltaAtomicApplyError("atomic Object-delta import action is invalid")
    if not changes:
        raise DedicatedObjectDeltaAtomicApplyError("apply plans must contain planned database changes")
    if receipt is None or cursor is None:
        raise DedicatedObjectDeltaAtomicApplyError(
            "apply plans require an immutable receipt and receiver cursor"
        )

    prior_sequence: int | None = None
    for change in changes:
        if type(change) is not PlannedObjectDeltaChange:
            raise DedicatedObjectDeltaAtomicApplyError("planned database change is invalid")
        try:
            require_object_delta_mvp_receiver_planned_change(change)
        except ObjectDeltaReceiverMvpHandlerError as exc:
            raise DedicatedObjectDeltaAtomicApplyError(
                "planned database change is not an authorized receiver handler intent"
            ) from exc
        sequence = change.logical_sequence
        if (
            type(sequence) is not int
            or sequence < 1
            or (prior_sequence is not None and sequence != prior_sequence + 1)
        ):
            raise DedicatedObjectDeltaAtomicApplyError(
                "planned database changes must have ascending contiguous logical sequences"
            )
        prior_sequence = sequence
    return action, changes, receipt, cursor


async def apply_atomic_object_delta_plan(
    *,
    plan: AtomicObjectDeltaImportPlan,
    adapter: DedicatedObjectDeltaApplyAdapter,
) -> AtomicObjectDeltaApplyResult:
    """Execute one pure import decision through a constrained transaction.

    The caller must obtain ``plan`` from the separate validation/planning
    layer using signature-verified, fixed-bucket, age-authenticated input and
    locked receiver state.  This helper makes no attempt to obtain or verify
    those inputs.  It only preserves the all-or-nothing transaction ordering
    once an already-valid apply decision is supplied.
    """

    action, changes, receipt, cursor = _validate_apply_plan(plan)
    if action == IMPORT_ACTION_REPLAY:
        return AtomicObjectDeltaApplyResult(action=action, changes_applied=0)

    _require_contract_name(getattr(adapter, "contract_name", None), label="adapter")
    begin = _require_async_method(
        getattr(adapter, "begin_atomic_object_delta_apply", None), label="adapter begin method"
    )
    transaction = await begin()
    try:
        _require_contract_name(
            getattr(transaction, "contract_name", None), label="transaction"
        )
        apply_change = _require_async_method(
            getattr(transaction, "apply_db_change", None), label="transaction apply method"
        )
        insert_receipt = _require_async_method(
            getattr(transaction, "insert_immutable_receipt", None), label="transaction receipt method"
        )
        write_cursor = _require_async_method(
            getattr(transaction, "write_receiver_cursor", None), label="transaction cursor method"
        )
        commit = _require_async_method(
            getattr(transaction, "commit", None), label="transaction commit method"
        )
        rollback = _require_async_method(
            getattr(transaction, "rollback", None), label="transaction rollback method"
        )
    except BaseException:
        rollback_candidate = getattr(transaction, "rollback", None)
        if callable(rollback_candidate):
            await rollback_candidate()
        raise

    try:
        for change in changes:
            await apply_change(change)
        await insert_receipt(receipt)
        await write_cursor(cursor)
        await commit()
    except BaseException:
        try:
            await rollback()
        except BaseException as rollback_error:
            raise DedicatedObjectDeltaAtomicApplyError(
                "Object-delta transaction failed and its rollback failed"
            ) from rollback_error
        raise

    return AtomicObjectDeltaApplyResult(action=action, changes_applied=len(changes))
