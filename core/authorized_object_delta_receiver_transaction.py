"""Default-off coordinator for one authorized Object-delta receiver apply.

This module is an isolated composition boundary, not a receiver runtime.  It
does not open a database session itself, contact Object Storage, decrypt age
payloads, read configuration, or enable any application path.  A
deployment-specific adapter must enter ``authorized_object_delta_receiver_apply_scope``
on one fresh session, lock all import and nonce state there, and expose only
the constrained transaction protocol below.

Mutable plans are intentionally never accepted from a caller.  The adapter
must derive them *after* it opens the single authorized transaction and holds
the receiver stream, Object-version, and nonce locks.  That prevents a caller
from smuggling a stale precomputed import decision into a later transaction.
An exact replay still enters that lock scope, performs no write, and rolls the
read-only transaction back to release its locks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from core.object_delta_import_plan import (
    IMPORT_ACTION_APPLY,
    IMPORT_ACTION_REPLAY,
    AtomicObjectDeltaImportPlan,
    ObjectDeltaImportReceipt,
    PlannedObjectDeltaChange,
    ReceiverStreamCursor,
    expected_import_receipt,
)
from core.object_delta_receiver_mvp_handlers import (
    ObjectDeltaReceiverMvpHandlerError,
    require_object_delta_mvp_receiver_planned_change,
)
from core.object_delta_delivery_control_packet import controller_key_id_from_public_key
from core.object_delta_receiver_apply_scope import (
    AuthorizedObjectDeltaReceiverDelivery,
    validate_authorized_object_delta_receiver_delivery,
)
from core.object_delta_receiver_genesis_admission import (
    AuthorizedObjectDeltaReceiverGenesisAdmission,
    ObjectDeltaReceiverGenesisAdmissionError,
    require_object_delta_receiver_genesis_admission,
)
from core.object_delta_receiver_delivery_nonce import (
    RECEIVER_DELIVERY_NONCE_ACTION_CONSUME,
    RECEIVER_DELIVERY_NONCE_ACTION_REPLAY,
    ObjectDeltaReceiverDeliveryNoncePlan,
    ObjectDeltaReceiverDeliveryNonceReceipt,
    expected_object_delta_receiver_delivery_nonce_receipt,
)


AUTHORIZED_OBJECT_DELTA_RECEIVER_TRANSACTION_CONTRACT = (
    "gold-trade-authorized-object-delta-receiver-transaction-v1"
)
AUTHORIZED_RECEIVER_TRANSACTION_ACTION_APPLY = "apply"
AUTHORIZED_RECEIVER_TRANSACTION_ACTION_NONCE_ONLY = "consume_nonce_only"
AUTHORIZED_RECEIVER_TRANSACTION_ACTION_REPLAY = "replay"


class AuthorizedObjectDeltaReceiverTransactionError(ValueError):
    """An authorized receiver import cannot preserve its atomic fences."""


@dataclass(frozen=True)
class LockedAuthorizedObjectDeltaReceiverPlans:
    """The only plans a live coordinator may execute.

    A transaction implementation creates this value only after locking the
    stream/cursor, immutable Object receipt, and controller nonce in its one
    active transaction.  It is deliberately not an input to the public
    coordinator function.
    """

    import_plan: AtomicObjectDeltaImportPlan
    nonce_plan: ObjectDeltaReceiverDeliveryNoncePlan


@runtime_checkable
class AuthorizedObjectDeltaReceiverTransaction(Protocol):
    """One fresh, scope-authorized database transaction.

    The concrete implementation must keep every method on the exact session
    and connection marked by the receiver apply scope.  It must neither open
    nor commit a nested transaction, and may not invoke legacy sync, HTTP,
    Object Storage, realtime, cache, Telegram, or audit side effects.
    """

    contract_name: str

    async def load_locked_authorized_object_delta_receiver_plans(
        self,
        *,
        authorization: AuthorizedObjectDeltaReceiverDelivery,
        observed_at: datetime,
    ) -> LockedAuthorizedObjectDeltaReceiverPlans:
        """Lock receiver state and build both plans in this transaction."""

    async def apply_db_change(self, change: PlannedObjectDeltaChange) -> None:
        """Apply exactly one normalized, already-planned database change."""

    async def insert_immutable_receipt(self, receipt: ObjectDeltaImportReceipt) -> None:
        """Persist the immutable Object-version receipt in this transaction."""

    async def consume_delivery_nonce(
        self,
        receipt: ObjectDeltaReceiverDeliveryNonceReceipt,
    ) -> None:
        """Persist the exact planned nonce receipt without committing."""

    async def write_receiver_cursor(self, cursor: ReceiverStreamCursor) -> None:
        """Persist the next receiver cursor in this transaction."""

    async def commit(self) -> None:
        """Commit exactly once after every required write succeeds."""

    async def rollback(self) -> None:
        """Roll back this same transaction after any failure or read-only replay."""


@runtime_checkable
class AuthorizedObjectDeltaReceiverTransactionAdapter(Protocol):
    """Factory for the constrained transaction."""

    contract_name: str

    async def begin_authorized_object_delta_receiver_transaction(
        self,
        *,
        authorization: AuthorizedObjectDeltaReceiverDelivery,
    ) -> AuthorizedObjectDeltaReceiverTransaction:
        """Open one fresh scope-authorized transaction for this delivery."""


@dataclass(frozen=True)
class AuthorizedObjectDeltaReceiverTransactionResult:
    """Non-secret outcome of an isolated authorized receiver transaction."""

    action: str
    import_action: str
    nonce_action: str
    changes_applied: int


@dataclass(frozen=True)
class _DeliveryExpectations:
    authorization: AuthorizedObjectDeltaReceiverDelivery
    import_receipt: ObjectDeltaImportReceipt
    cursor: ReceiverStreamCursor
    nonce_receipt: ObjectDeltaReceiverDeliveryNonceReceipt


@dataclass(frozen=True)
class _ValidatedCoordinatorPlans:
    import_plan: AtomicObjectDeltaImportPlan
    nonce_receipt: ObjectDeltaReceiverDeliveryNonceReceipt | None
    import_action: str
    nonce_action: str
    action: str


REQUIRED_AUTHORIZED_OBJECT_DELTA_RECEIVER_TRANSACTION_INVARIANTS = (
    "validate the signed packet/batch nonce receipt against observed_at before any database work",
    "require an exact opaque genesis-admission capability before any sequence-one batch opens a transaction",
    "accept no caller-supplied mutable import or nonce plan",
    "open one fresh receiver-scope transaction before locking and deriving mutable plans",
    "lock/load receiver cursor, immutable import receipt, and controller nonce before deriving both plans",
    "fresh import plus fresh nonce applies changes, inserts immutable receipt, consumes nonce, writes cursor, then commits once",
    "existing import plus fresh nonce consumes only the nonce and commits once",
    "exact import plus exact nonce replay performs no write and rolls back the locked transaction",
    "a replayed nonce paired with a fresh import fails closed and rolls back",
    "receipt insertion precedes nonce consumption so the nonce-to-import foreign key remains valid",
    "do not invoke legacy sync, HTTP, Object Storage, realtime, cache, Telegram, or audit behaviour",
)


def _derive_delivery_expectations(
    *,
    authorization: object,
    observed_at: datetime,
) -> _DeliveryExpectations:
    if type(authorization) is not AuthorizedObjectDeltaReceiverDelivery:
        raise AuthorizedObjectDeltaReceiverTransactionError("authorized receiver delivery is invalid")
    try:
        reauthorized = validate_authorized_object_delta_receiver_delivery(authorization)
        if reauthorized != authorization:
            raise AuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver delivery does not match its verified binding"
            )
        if (
            controller_key_id_from_public_key(reauthorized.binding.controller_public_key)
            != reauthorized.verified_packet.controller_key_id
        ):
            raise AuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver controller key does not match its verified packet"
            )
        batch = reauthorized.batch
        import_receipt = expected_import_receipt(batch)
        cursor = ReceiverStreamCursor(
            source_site=batch.source_site,
            destination_site=batch.destination_site,
            campaign_id=batch.campaign_id,
            release_sha=batch.release_sha,
            stream_generation_id=batch.stream.generation_id,
            last_sequence=batch.stream.last_sequence,
            last_batch_sha256=batch.batch_sha256,
        )
        nonce_receipt = expected_object_delta_receiver_delivery_nonce_receipt(
            packet=reauthorized.verified_packet,
            batch=batch,
            observed_at=observed_at,
        )
    except Exception as exc:
        raise AuthorizedObjectDeltaReceiverTransactionError(
            "authorized receiver delivery is not bound to a currently valid batch"
        ) from exc
    return _DeliveryExpectations(
        authorization=reauthorized,
        import_receipt=import_receipt,
        cursor=cursor,
        nonce_receipt=nonce_receipt,
    )


def _validate_import_plan(
    *,
    expectations: _DeliveryExpectations,
    import_plan: object,
) -> AtomicObjectDeltaImportPlan:
    if type(import_plan) is not AtomicObjectDeltaImportPlan:
        raise AuthorizedObjectDeltaReceiverTransactionError("locked immutable Object-delta import plan is invalid")
    if import_plan.action == IMPORT_ACTION_REPLAY:
        if (
            import_plan.receipt_to_insert is not None
            or import_plan.cursor_to_write is not None
            or import_plan.changes_to_apply
        ):
            raise AuthorizedObjectDeltaReceiverTransactionError(
                "locked immutable import replay plan contains writes"
            )
        return import_plan
    if import_plan.action != IMPORT_ACTION_APPLY:
        raise AuthorizedObjectDeltaReceiverTransactionError("locked immutable Object-delta import action is invalid")
    if import_plan.receipt_to_insert != expectations.import_receipt:
        raise AuthorizedObjectDeltaReceiverTransactionError(
            "locked immutable import receipt does not match the authorized delivery"
        )
    if import_plan.cursor_to_write != expectations.cursor:
        raise AuthorizedObjectDeltaReceiverTransactionError(
            "locked receiver cursor does not match the authorized delivery"
        )
    if not isinstance(import_plan.changes_to_apply, tuple) or not import_plan.changes_to_apply:
        raise AuthorizedObjectDeltaReceiverTransactionError(
            "locked immutable import apply plan has no ordered database changes"
        )
    actual_sequences: list[int] = []
    for change in import_plan.changes_to_apply:
        if type(change) is not PlannedObjectDeltaChange:
            raise AuthorizedObjectDeltaReceiverTransactionError(
                "locked immutable import apply plan contains an invalid database change"
            )
        try:
            require_object_delta_mvp_receiver_planned_change(change)
        except ObjectDeltaReceiverMvpHandlerError as exc:
            raise AuthorizedObjectDeltaReceiverTransactionError(
                "locked immutable import apply plan contains an unauthorized receiver handler"
            ) from exc
        actual_sequences.append(change.logical_sequence)
    if tuple(actual_sequences) != expectations.authorization.batch.stream.sequence_ids:
        raise AuthorizedObjectDeltaReceiverTransactionError(
            "locked immutable import changes do not match the authorized delivery sequence"
        )
    return import_plan


def _validate_nonce_plan(
    *,
    expectations: _DeliveryExpectations,
    nonce_plan: object,
) -> ObjectDeltaReceiverDeliveryNonceReceipt | None:
    if not isinstance(nonce_plan, ObjectDeltaReceiverDeliveryNoncePlan):
        raise AuthorizedObjectDeltaReceiverTransactionError("locked delivery nonce consumption plan is invalid")
    if nonce_plan.action == RECEIVER_DELIVERY_NONCE_ACTION_REPLAY:
        if nonce_plan.receipt_to_insert is not None:
            raise AuthorizedObjectDeltaReceiverTransactionError(
                "locked delivery nonce replay plan contains a write"
            )
        return None
    if nonce_plan.action != RECEIVER_DELIVERY_NONCE_ACTION_CONSUME:
        raise AuthorizedObjectDeltaReceiverTransactionError(
            "locked delivery nonce consumption action is invalid"
        )
    if nonce_plan.receipt_to_insert != expectations.nonce_receipt:
        raise AuthorizedObjectDeltaReceiverTransactionError(
            "locked delivery nonce receipt does not match the authorized receiver delivery"
        )
    return expectations.nonce_receipt


def _validate_locked_plans(
    *,
    expectations: _DeliveryExpectations,
    value: object,
) -> _ValidatedCoordinatorPlans:
    if not isinstance(value, LockedAuthorizedObjectDeltaReceiverPlans):
        raise AuthorizedObjectDeltaReceiverTransactionError("locked authorized receiver plans are invalid")
    import_plan = _validate_import_plan(
        expectations=expectations,
        import_plan=value.import_plan,
    )
    nonce_receipt = _validate_nonce_plan(
        expectations=expectations,
        nonce_plan=value.nonce_plan,
    )
    nonce_action = value.nonce_plan.action
    if import_plan.action == IMPORT_ACTION_APPLY:
        if nonce_receipt is None:
            raise AuthorizedObjectDeltaReceiverTransactionError(
                "fresh immutable import cannot reuse an already consumed delivery nonce"
            )
        action = AUTHORIZED_RECEIVER_TRANSACTION_ACTION_APPLY
    elif nonce_receipt is not None:
        action = AUTHORIZED_RECEIVER_TRANSACTION_ACTION_NONCE_ONLY
    else:
        action = AUTHORIZED_RECEIVER_TRANSACTION_ACTION_REPLAY
    return _ValidatedCoordinatorPlans(
        import_plan=import_plan,
        nonce_receipt=nonce_receipt,
        import_action=import_plan.action,
        nonce_action=nonce_action,
        action=action,
    )


def _require_contract_name(value: object, *, label: str) -> None:
    if value != AUTHORIZED_OBJECT_DELTA_RECEIVER_TRANSACTION_CONTRACT:
        raise AuthorizedObjectDeltaReceiverTransactionError(
            f"{label} does not declare the authorized Object-delta receiver transaction contract"
        )


def _require_async_method(value: object, *, label: str) -> Callable[..., Awaitable[object]]:
    if not callable(value):
        raise AuthorizedObjectDeltaReceiverTransactionError(f"{label} is missing")
    return value


def _replay_result() -> AuthorizedObjectDeltaReceiverTransactionResult:
    return AuthorizedObjectDeltaReceiverTransactionResult(
        action=AUTHORIZED_RECEIVER_TRANSACTION_ACTION_REPLAY,
        import_action=IMPORT_ACTION_REPLAY,
        nonce_action=RECEIVER_DELIVERY_NONCE_ACTION_REPLAY,
        changes_applied=0,
    )


async def coordinate_authorized_object_delta_receiver_transaction(
    *,
    authorization: AuthorizedObjectDeltaReceiverDelivery,
    observed_at: datetime,
    adapter: AuthorizedObjectDeltaReceiverTransactionAdapter,
    genesis_admission: AuthorizedObjectDeltaReceiverGenesisAdmission | None = None,
) -> AuthorizedObjectDeltaReceiverTransactionResult:
    """Execute one locked authorized receiver decision, or a locked no-op replay.

    ``adapter`` owns the only database session and must use the receiver apply
    scope before it returns a transaction.  For a sequence-one batch,
    ``genesis_admission`` must be the opaque capability minted from exact
    independently verified baseline, cutover, and local restore evidence. A
    later batch must not receive that one-shot capability. The coordinator
    intentionally takes no mutable plans: its transaction loads locked state
    and derives both plans after ``begin``. This preserves the
    receipt-before-nonce order needed by the durable nonce foreign key and
    prevents stale replay decisions.
    """

    expectations = _derive_delivery_expectations(
        authorization=authorization,
        observed_at=observed_at,
    )
    if expectations.authorization.batch.stream.first_sequence == 1:
        if genesis_admission is None:
            raise AuthorizedObjectDeltaReceiverTransactionError(
                "sequence-one receiver delivery requires verified genesis admission before transaction begin"
            )
        try:
            admitted_authorization = require_object_delta_receiver_genesis_admission(
                authorization=expectations.authorization,
                admission=genesis_admission,
            )
        except ObjectDeltaReceiverGenesisAdmissionError as exc:
            raise AuthorizedObjectDeltaReceiverTransactionError(
                "sequence-one receiver delivery genesis admission is invalid before transaction begin"
            ) from exc
        if admitted_authorization is not expectations.authorization:
            raise AuthorizedObjectDeltaReceiverTransactionError(
                "sequence-one receiver delivery genesis admission does not preserve its exact authorization"
            )
    elif genesis_admission is not None:
        raise AuthorizedObjectDeltaReceiverTransactionError(
            "non-genesis receiver delivery must not receive a genesis admission"
        )
    _require_contract_name(getattr(adapter, "contract_name", None), label="adapter")
    begin = _require_async_method(
        getattr(adapter, "begin_authorized_object_delta_receiver_transaction", None),
        label="adapter begin method",
    )
    transaction = await begin(authorization=expectations.authorization)
    rollback_attempted = False
    try:
        _require_contract_name(getattr(transaction, "contract_name", None), label="transaction")
        rollback = _require_async_method(
            getattr(transaction, "rollback", None),
            label="transaction rollback method",
        )
        load_locked_plans = _require_async_method(
            getattr(transaction, "load_locked_authorized_object_delta_receiver_plans", None),
            label="transaction locked-plan method",
        )
        locked_plans = await load_locked_plans(
            authorization=expectations.authorization,
            observed_at=observed_at,
        )
        validated = _validate_locked_plans(
            expectations=expectations,
            value=locked_plans,
        )
        if validated.action == AUTHORIZED_RECEIVER_TRANSACTION_ACTION_REPLAY:
            rollback_attempted = True
            await rollback()
            return _replay_result()
        apply_change = _require_async_method(
            getattr(transaction, "apply_db_change", None),
            label="transaction apply method",
        )
        insert_receipt = _require_async_method(
            getattr(transaction, "insert_immutable_receipt", None),
            label="transaction immutable receipt method",
        )
        consume_nonce = _require_async_method(
            getattr(transaction, "consume_delivery_nonce", None),
            label="transaction delivery nonce method",
        )
        write_cursor = _require_async_method(
            getattr(transaction, "write_receiver_cursor", None),
            label="transaction cursor method",
        )
        commit = _require_async_method(
            getattr(transaction, "commit", None),
            label="transaction commit method",
        )
    except BaseException:
        rollback_candidate = getattr(transaction, "rollback", None)
        if not rollback_attempted and callable(rollback_candidate):
            await rollback_candidate()
        raise

    try:
        if validated.action == AUTHORIZED_RECEIVER_TRANSACTION_ACTION_APPLY:
            for change in validated.import_plan.changes_to_apply:
                await apply_change(change)
            # The nonce row references this immutable receipt by Object version.
            await insert_receipt(validated.import_plan.receipt_to_insert)
        if validated.nonce_receipt is None:
            raise AuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction has no nonce receipt to consume"
            )
        await consume_nonce(validated.nonce_receipt)
        if validated.action == AUTHORIZED_RECEIVER_TRANSACTION_ACTION_APPLY:
            await write_cursor(validated.import_plan.cursor_to_write)
        await commit()
    except BaseException:
        try:
            rollback_attempted = True
            await rollback()
        except BaseException as rollback_error:
            raise AuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction failed and its rollback failed"
            ) from rollback_error
        raise

    return AuthorizedObjectDeltaReceiverTransactionResult(
        action=validated.action,
        import_action=validated.import_action,
        nonce_action=validated.nonce_action,
        changes_applied=(
            len(validated.import_plan.changes_to_apply)
            if validated.action == AUTHORIZED_RECEIVER_TRANSACTION_ACTION_APPLY
            else 0
        ),
    )
