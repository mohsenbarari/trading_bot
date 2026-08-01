"""Concrete, still-default-off SQLAlchemy receiver transaction adapter.

This module is intentionally the narrow persistence half of the Object-delta
receiver.  It accepts a caller-supplied ``AsyncSession`` factory and an
already-admitted opaque payload; it neither obtains transport bytes nor knows
about Object Storage, age, URLs, credentials, workers, or global database
configuration.

Only the release-pinned ``commodities``/``INSERT`` handler is executable.
Its SQL is a natural-key ``INSERT ... ON CONFLICT DO NOTHING``.  No generic
upsert, local-id translation, UPDATE, delete, or legacy sync endpoint can
reach this adapter.

The adapter deliberately remains an integration boundary rather than runtime
wiring.  A caller must already have verified/decrypted a delivery, minted the
payload-admission capability, and independently satisfied the sequence-one
genesis gate before passing this adapter to the coordinator.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
import hashlib
import inspect
import json

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.authorized_object_delta_receiver_transaction import (
    AUTHORIZED_OBJECT_DELTA_RECEIVER_TRANSACTION_CONTRACT,
    LockedAuthorizedObjectDeltaReceiverPlans,
)
from core.object_delta_import_plan import (
    AtomicObjectDeltaImportPlan,
    IMPORT_ACTION_REPLAY,
    ObjectDeltaImportReceipt as ImportReceipt,
    PlannedObjectDeltaChange,
    ReceiverStreamCursor,
    expected_import_receipt,
)
from core.object_delta_receiver_apply_scope import (
    AuthorizedObjectDeltaReceiverDelivery,
    ObjectDeltaReceiverApplyScopeError,
    authorized_object_delta_receiver_apply_scope,
    session_is_authorized_for_object_delta_receiver_apply,
    validate_authorized_object_delta_receiver_delivery,
)
from core.object_delta_receiver_delivery_nonce import (
    RECEIVER_DELIVERY_NONCE_ACTION_REPLAY,
    ObjectDeltaReceiverDeliveryNonceError,
    ObjectDeltaReceiverDeliveryNonceReceipt as NonceReceipt,
    expected_object_delta_receiver_delivery_nonce_receipt,
    plan_object_delta_receiver_delivery_nonce_consumption,
    validate_object_delta_receiver_delivery_nonce_receipt,
)
from core.object_delta_receiver_delivery_nonce_persistence import (
    ObjectDeltaReceiverDeliveryNoncePersistenceError,
    persist_object_delta_receiver_delivery_nonce,
    receiver_delivery_nonce_advisory_lock_key,
)
from core.object_delta_receiver_mvp_handlers import (
    COMMODITIES_TABLE,
    COMMODITY_ENSURE_CONFLICT_POLICY,
    INSERT,
    CommodityEnsureIntent,
    ObjectDeltaReceiverMvpHandlerError,
    require_object_delta_mvp_receiver_planned_change,
)
from core.object_delta_receiver_payload_admission import (
    AuthorizedObjectDeltaReceiverPayload,
    ObjectDeltaReceiverPayloadAdmissionError,
    plan_authorized_object_delta_receiver_payload_import,
    require_authorized_object_delta_receiver_payload,
)
from models.commodity import Commodity
from models.object_delta import (
    ObjectDeltaImportReceipt as ImportReceiptModel,
    ObjectDeltaReceiverCursor as ReceiverCursorModel,
)
from models.object_delta_receiver_delivery import (
    ObjectDeltaReceiverDeliveryNonceReceipt as NonceReceiptModel,
)


SQLALCHEMY_AUTHORIZED_OBJECT_DELTA_RECEIVER_TRANSACTION_CONTRACT = (
    "gold-trade-sqlalchemy-authorized-object-delta-receiver-transaction-v1"
)


class SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(RuntimeError):
    """The concrete receiver transaction cannot preserve its fences."""


def _advisory_key(*, namespace: str, value: dict[str, object]) -> int:
    payload = {"namespace": namespace, **value}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def receiver_stream_advisory_lock_key(
    authorization: AuthorizedObjectDeltaReceiverDelivery,
) -> int:
    """Return a stable lock key for one receiver stream identity."""

    try:
        verified = validate_authorized_object_delta_receiver_delivery(authorization)
        batch = verified.batch
        return _advisory_key(
            namespace="gold-trade-object-delta-receiver-stream-v1",
            value={
                "source_site": batch.source_site,
                "destination_site": batch.destination_site,
                "campaign_id": batch.campaign_id,
                "release_sha": batch.release_sha,
                "stream_generation_id": batch.stream.generation_id,
            },
        )
    except Exception as exc:
        raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
            "authorized receiver stream lock identity is invalid"
        ) from exc


def receiver_object_advisory_lock_key(receipt: ImportReceipt) -> int:
    """Return a stable lock key for an immutable receiver Object version."""

    if type(receipt) is not ImportReceipt:
        raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
            "authorized receiver object lock receipt is invalid"
        )
    return _advisory_key(
        namespace="gold-trade-object-delta-receiver-object-v1",
        value={
            "source_site": receipt.source_site,
            "destination_site": receipt.destination_site,
            "campaign_id": receipt.campaign_id,
            "release_sha": receipt.release_sha,
            "stream_generation_id": receipt.stream_generation_id,
            "object_key": receipt.object_key,
            "object_version_id": receipt.object_version_id,
        },
    )


def _require_async_callable(value: object, *, label: str) -> Callable[..., Awaitable[object]]:
    if not callable(value):
        raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(f"{label} is unavailable")
    return value


async def _await_session_close(session: object) -> None:
    close = _require_async_callable(getattr(session, "close", None), label="receiver session close")
    result = close()
    if not inspect.isawaitable(result):
        raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
            "receiver session close is not asynchronous"
        )
    await result


def _session_has_active_transaction(session: object) -> bool:
    checker = getattr(session, "in_transaction", None)
    if not callable(checker):
        return False
    try:
        return bool(checker())
    except Exception:
        return False


async def _scalar_one_or_none(session: object, statement: object, *, label: str) -> object | None:
    execute = _require_async_callable(getattr(session, "execute", None), label="receiver session execute")
    try:
        result = await execute(statement)
        scalar = getattr(result, "scalar_one_or_none", None)
        if not callable(scalar):
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                f"receiver {label} query has no scalar result"
            )
        return scalar()
    except SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError:
        raise
    except Exception as exc:
        raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
            f"receiver {label} query failed"
        ) from exc


async def _execute(session: object, statement: object, *, label: str) -> object:
    execute = _require_async_callable(getattr(session, "execute", None), label="receiver session execute")
    try:
        return await execute(statement)
    except SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError:
        raise
    except Exception as exc:
        raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
            f"receiver {label} execution failed"
        ) from exc


async def _flush(session: object, *, label: str) -> None:
    flush = _require_async_callable(getattr(session, "flush", None), label="receiver session flush")
    try:
        await flush()
    except SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError:
        raise
    except Exception as exc:
        raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
            f"receiver {label} flush failed"
        ) from exc


def _model_cursor(row: object | None) -> ReceiverStreamCursor | None:
    if row is None:
        return None
    if not isinstance(row, ReceiverCursorModel):
        raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
            "locked receiver cursor row is invalid"
        )
    return ReceiverStreamCursor(
        source_site=row.source_site,
        destination_site=row.destination_site,
        campaign_id=row.campaign_id,
        release_sha=row.release_sha,
        stream_generation_id=row.stream_generation_id,
        last_sequence=row.last_sequence,
        last_batch_sha256=row.last_batch_sha256,
    )


def _model_import_receipt(row: object | None) -> ImportReceipt | None:
    if row is None:
        return None
    if not isinstance(row, ImportReceiptModel):
        raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
            "locked immutable import receipt row is invalid"
        )
    return ImportReceipt(
        source_site=row.source_site,
        destination_site=row.destination_site,
        campaign_id=row.campaign_id,
        release_sha=row.release_sha,
        stream_generation_id=row.stream_generation_id,
        first_sequence=row.first_sequence,
        last_sequence=row.last_sequence,
        writer_epoch=row.writer_epoch,
        writer_lease_id=row.writer_lease_id,
        prior_chain_sha256=row.prior_chain_sha256,
        batch_sha256=row.batch_sha256,
        payload_sha256=row.payload_sha256,
        object_key=row.object_key,
        object_version_id=row.object_version_id,
        ciphertext_sha256=row.ciphertext_sha256,
        ciphertext_bytes=row.ciphertext_bytes,
    )


def _model_nonce_receipt(row: object | None) -> NonceReceipt | None:
    if row is None:
        return None
    if not isinstance(row, NonceReceiptModel):
        raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
            "locked delivery nonce row is invalid"
        )
    try:
        return validate_object_delta_receiver_delivery_nonce_receipt(
            NonceReceipt(
                controller_key_id=row.controller_key_id,
                nonce=row.nonce,
                packet_claim_sha256=row.packet_claim_sha256,
                bucket=row.bucket,
                source_site=row.source_site,
                destination_site=row.destination_site,
                destination_age_recipient=row.destination_age_recipient,
                campaign_id=row.campaign_id,
                release_sha=row.release_sha,
                stream_generation_id=row.stream_generation_id,
                writer_epoch=row.writer_epoch,
                writer_lease_id=row.writer_lease_id,
                first_sequence=row.first_sequence,
                last_sequence=row.last_sequence,
                batch_sha256=row.batch_sha256,
                object_key=row.object_key,
                object_version_id=row.object_version_id,
                expires_at=row.expires_at,
            )
        )
    except ObjectDeltaReceiverDeliveryNonceError as exc:
        raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
            "locked delivery nonce row is invalid"
        ) from exc


def _receipt_model(receipt: ImportReceipt) -> ImportReceiptModel:
    if type(receipt) is not ImportReceipt:
        raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
            "immutable import receipt is invalid"
        )
    return ImportReceiptModel(
        source_site=receipt.source_site,
        destination_site=receipt.destination_site,
        campaign_id=receipt.campaign_id,
        release_sha=receipt.release_sha,
        stream_generation_id=receipt.stream_generation_id,
        first_sequence=receipt.first_sequence,
        last_sequence=receipt.last_sequence,
        writer_epoch=receipt.writer_epoch,
        writer_lease_id=receipt.writer_lease_id,
        prior_chain_sha256=receipt.prior_chain_sha256,
        batch_sha256=receipt.batch_sha256,
        payload_sha256=receipt.payload_sha256,
        object_key=receipt.object_key,
        object_version_id=receipt.object_version_id,
        ciphertext_sha256=receipt.ciphertext_sha256,
        ciphertext_bytes=receipt.ciphertext_bytes,
    )


def _same_stream_identity(row: ReceiverCursorModel, cursor: ReceiverStreamCursor) -> bool:
    return (
        row.source_site,
        row.destination_site,
        row.campaign_id,
        row.release_sha,
        row.stream_generation_id,
    ) == (
        cursor.source_site,
        cursor.destination_site,
        cursor.campaign_id,
        cursor.release_sha,
        cursor.stream_generation_id,
    )


@dataclass(frozen=True)
class _LockedRows:
    cursor_row: ReceiverCursorModel | None
    cursor: ReceiverStreamCursor | None
    receipt_by_object: ImportReceipt | None
    receipt_by_stream: ImportReceipt | None
    nonce: NonceReceipt | None


class SqlAlchemyAuthorizedObjectDeltaReceiverTransaction:
    """One scope-marked, single-session concrete coordinator transaction."""

    contract_name = AUTHORIZED_OBJECT_DELTA_RECEIVER_TRANSACTION_CONTRACT

    def __init__(
        self,
        *,
        session: AsyncSession | object,
        authorization: AuthorizedObjectDeltaReceiverDelivery,
        payload_admission: AuthorizedObjectDeltaReceiverPayload,
        scope: object,
    ) -> None:
        self._session = session
        self._authorization = authorization
        self._payload_admission = payload_admission
        self._scope = scope
        self._closed = False
        self._terminal = False
        self._locked_rows: _LockedRows | None = None
        self._plans: LockedAuthorizedObjectDeltaReceiverPlans | None = None
        self._expected_nonce_receipt: NonceReceipt | None = None
        self._inserted_receipt: ImportReceipt | None = None
        self._applied_change_count = 0
        self._nonce_consumed = False
        self._cursor_written = False

    def _require_open_scope(self) -> None:
        if self._closed or self._scope is None:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction is closed"
            )
        if self._terminal:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction is already terminal"
            )
        if not _session_has_active_transaction(self._session):
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction has no active session transaction"
            )
        if not session_is_authorized_for_object_delta_receiver_apply(self._session):
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction lost its receiver apply scope"
            )

    async def _close_scope_and_session(self) -> None:
        if self._closed:
            return
        self._closed = True
        scope = self._scope
        self._scope = None
        scope_error: BaseException | None = None
        try:
            if scope is not None:
                exit_scope = getattr(scope, "__aexit__", None)
                if not callable(exit_scope):
                    raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                        "authorized receiver transaction scope is invalid"
                    )
                await exit_scope(None, None, None)
        except BaseException as exc:
            scope_error = exc
        try:
            await _await_session_close(self._session)
        except BaseException as close_error:
            if scope_error is not None:
                raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                    "authorized receiver transaction scope and session close both failed"
                ) from close_error
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction session close failed"
            ) from close_error
        if scope_error is not None:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction scope close failed"
            ) from scope_error

    async def _lock_advisory(self, key: int, *, label: str) -> None:
        await _scalar_one_or_none(
            self._session,
            select(func.pg_advisory_xact_lock(key)),
            label=f"{label} advisory lock",
        )

    async def _load_locked_rows(
        self,
        *,
        import_receipt: ImportReceipt,
        nonce_receipt: NonceReceipt,
    ) -> _LockedRows:
        batch = self._authorization.batch
        # The category order is fixed for every attempt.  The stream lock
        # serializes range/cursor decisions; then Object and nonce identities
        # make independently retried delivery evidence deterministic too.
        await self._lock_advisory(
            receiver_stream_advisory_lock_key(self._authorization),
            label="stream",
        )
        await self._lock_advisory(
            receiver_object_advisory_lock_key(import_receipt),
            label="object",
        )
        await self._lock_advisory(
            receiver_delivery_nonce_advisory_lock_key(nonce_receipt),
            label="nonce",
        )

        cursor_row = await _scalar_one_or_none(
            self._session,
            select(ReceiverCursorModel)
            .where(
                ReceiverCursorModel.source_site == batch.source_site,
                ReceiverCursorModel.destination_site == batch.destination_site,
                ReceiverCursorModel.campaign_id == batch.campaign_id,
                ReceiverCursorModel.release_sha == batch.release_sha,
                ReceiverCursorModel.stream_generation_id == batch.stream.generation_id,
            )
            .with_for_update(),
            label="receiver cursor",
        )
        object_receipt_row = await _scalar_one_or_none(
            self._session,
            select(ImportReceiptModel)
            .where(
                ImportReceiptModel.object_key == import_receipt.object_key,
                ImportReceiptModel.object_version_id == import_receipt.object_version_id,
            )
            .with_for_update(),
            label="immutable object receipt",
        )
        stream_receipt_row = await _scalar_one_or_none(
            self._session,
            select(ImportReceiptModel)
            .where(
                ImportReceiptModel.source_site == import_receipt.source_site,
                ImportReceiptModel.destination_site == import_receipt.destination_site,
                ImportReceiptModel.campaign_id == import_receipt.campaign_id,
                ImportReceiptModel.release_sha == import_receipt.release_sha,
                ImportReceiptModel.stream_generation_id == import_receipt.stream_generation_id,
                ImportReceiptModel.first_sequence == import_receipt.first_sequence,
            )
            .with_for_update(),
            label="logical stream receipt",
        )
        nonce_row = await _scalar_one_or_none(
            self._session,
            select(NonceReceiptModel)
            .where(
                NonceReceiptModel.controller_key_id == nonce_receipt.controller_key_id,
                NonceReceiptModel.nonce == nonce_receipt.nonce,
            )
            .with_for_update(),
            label="delivery nonce receipt",
        )
        return _LockedRows(
            cursor_row=cursor_row if isinstance(cursor_row, ReceiverCursorModel) else None,
            cursor=_model_cursor(cursor_row),
            receipt_by_object=_model_import_receipt(object_receipt_row),
            receipt_by_stream=_model_import_receipt(stream_receipt_row),
            nonce=_model_nonce_receipt(nonce_row),
        )

    async def load_locked_authorized_object_delta_receiver_plans(
        self,
        *,
        authorization: AuthorizedObjectDeltaReceiverDelivery,
        observed_at: datetime,
    ) -> LockedAuthorizedObjectDeltaReceiverPlans:
        """Lock all mutable state, then derive import and nonce plans once."""

        self._require_open_scope()
        if self._plans is not None:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction plans were already loaded"
            )
        if authorization is not self._authorization:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction delivery does not match its fresh scope"
            )
        try:
            verified = validate_authorized_object_delta_receiver_delivery(authorization)
            admitted = require_authorized_object_delta_receiver_payload(self._payload_admission)
            if admitted.authorization is not verified:
                raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                    "authorized receiver payload does not match the scoped delivery"
                )
            import_receipt = expected_import_receipt(verified.batch)
            nonce_receipt = expected_object_delta_receiver_delivery_nonce_receipt(
                packet=verified.verified_packet,
                batch=verified.batch,
                observed_at=observed_at,
            )
        except SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError:
            raise
        except (
            ObjectDeltaReceiverApplyScopeError,
            ObjectDeltaReceiverPayloadAdmissionError,
            ObjectDeltaReceiverDeliveryNonceError,
            ValueError,
        ) as exc:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver delivery or payload is invalid before lock acquisition"
            ) from exc

        locked = await self._load_locked_rows(
            import_receipt=import_receipt,
            nonce_receipt=nonce_receipt,
        )
        # The pure planners run only after all three advisory locks and every
        # FOR UPDATE lookup above.  No caller can inject a stale plan here.
        try:
            import_plan = plan_authorized_object_delta_receiver_payload_import(
                payload_admission=admitted,
                receiver_cursor=locked.cursor,
                receipt_by_object=locked.receipt_by_object,
                receipt_by_stream=locked.receipt_by_stream,
            )
            nonce_plan = plan_object_delta_receiver_delivery_nonce_consumption(
                expected=nonce_receipt,
                existing=locked.nonce,
            )
        except (ObjectDeltaReceiverPayloadAdmissionError, ObjectDeltaReceiverDeliveryNonceError) as exc:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "locked authorized receiver state cannot derive a safe plan"
            ) from exc
        if type(import_plan) is not AtomicObjectDeltaImportPlan:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "locked authorized receiver import plan is invalid"
            )
        self._locked_rows = locked
        self._expected_nonce_receipt = nonce_receipt
        self._plans = LockedAuthorizedObjectDeltaReceiverPlans(
            import_plan=import_plan,
            nonce_plan=nonce_plan,
        )
        return self._plans

    async def apply_db_change(self, change: PlannedObjectDeltaChange) -> None:
        """Execute only the opaque commodities natural-key ensure handler."""

        self._require_open_scope()
        if self._plans is None:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver change was not derived from locked plans"
            )
        expected_changes = self._plans.import_plan.changes_to_apply
        if (
            self._applied_change_count >= len(expected_changes)
            or change is not expected_changes[self._applied_change_count]
        ):
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver change does not match the next locked plan change"
            )
        try:
            planned = require_object_delta_mvp_receiver_planned_change(change)
            intent = planned.intent
        except ObjectDeltaReceiverMvpHandlerError as exc:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver change has no executable handler"
            ) from exc
        if (
            type(intent) is not CommodityEnsureIntent
            or intent.table != COMMODITIES_TABLE
            or intent.operation != INSERT
            or intent.conflict_policy != COMMODITY_ENSURE_CONFLICT_POLICY
        ):
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver change widens the commodities insert handler"
            )
        statement = postgresql_insert(Commodity).values(name=intent.name).on_conflict_do_nothing(
            index_elements=(Commodity.name,)
        )
        await _execute(self._session, statement, label="commodities insert-on-conflict")
        self._applied_change_count += 1

    async def insert_immutable_receipt(self, receipt: ImportReceipt) -> None:
        """Flush the immutable receipt before a nonce can reference it."""

        self._require_open_scope()
        if self._plans is None or self._plans.import_plan.receipt_to_insert != receipt:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "immutable receiver receipt does not match locked plans"
            )
        if self._applied_change_count != len(self._plans.import_plan.changes_to_apply):
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "every locked receiver change must apply before immutable receipt insertion"
            )
        if self._inserted_receipt is not None:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "immutable receiver receipt was already inserted"
            )
        add = getattr(self._session, "add", None)
        if not callable(add):
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "receiver session add is unavailable"
            )
        try:
            add(_receipt_model(receipt))
        except Exception as exc:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "immutable receiver receipt insert failed"
            ) from exc
        await _flush(self._session, label="immutable receiver receipt")
        self._inserted_receipt = receipt

    async def consume_delivery_nonce(self, receipt: NonceReceipt) -> None:
        """Persist the prelocked nonce through the existing narrow adapter."""

        self._require_open_scope()
        if self._plans is None or self._expected_nonce_receipt != receipt:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "delivery nonce receipt does not match locked plans"
            )
        if self._nonce_consumed:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "delivery nonce was already consumed in this transaction"
            )
        if self._plans.import_plan.receipt_to_insert is not None and self._inserted_receipt is None:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "immutable receiver receipt must be inserted before nonce consumption"
            )
        try:
            persisted = await persist_object_delta_receiver_delivery_nonce(self._session, receipt)
        except ObjectDeltaReceiverDeliveryNoncePersistenceError as exc:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "locked delivery nonce could not be persisted"
            ) from exc
        if persisted.action != self._plans.nonce_plan.action or persisted.receipt != receipt:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "delivery nonce persistence does not match locked plans"
            )
        self._nonce_consumed = True

    async def write_receiver_cursor(self, cursor: ReceiverStreamCursor) -> None:
        """Create or advance only the cursor derived from locked state."""

        self._require_open_scope()
        if self._plans is None or self._plans.import_plan.cursor_to_write != cursor:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "receiver cursor does not match locked plans"
            )
        if not self._nonce_consumed:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "delivery nonce must be consumed before receiver cursor write"
            )
        if self._cursor_written:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "receiver cursor was already written in this transaction"
            )
        if self._locked_rows is None:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "receiver cursor has no locked row state"
            )
        row = self._locked_rows.cursor_row
        if row is None:
            add = getattr(self._session, "add", None)
            if not callable(add):
                raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                    "receiver session add is unavailable"
                )
            try:
                add(
                    ReceiverCursorModel(
                        source_site=cursor.source_site,
                        destination_site=cursor.destination_site,
                        campaign_id=cursor.campaign_id,
                        release_sha=cursor.release_sha,
                        stream_generation_id=cursor.stream_generation_id,
                        last_sequence=cursor.last_sequence,
                        last_batch_sha256=cursor.last_batch_sha256,
                    )
                )
            except Exception as exc:
                raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                    "receiver cursor insert failed"
                ) from exc
        else:
            if not _same_stream_identity(row, cursor):
                raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                    "locked receiver cursor identity changed"
                )
            row.last_sequence = cursor.last_sequence
            row.last_batch_sha256 = cursor.last_batch_sha256
        await _flush(self._session, label="receiver cursor")
        self._cursor_written = True

    async def commit(self) -> None:
        """Commit once, then unmark and close the one factory session."""

        self._require_open_scope()
        if self._plans is None:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction has no locked plans to commit"
            )
        if (
            self._plans.import_plan.action == IMPORT_ACTION_REPLAY
            and self._plans.nonce_plan.action == RECEIVER_DELIVERY_NONCE_ACTION_REPLAY
        ):
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "locked receiver replay must roll back rather than commit"
            )
        if not self._nonce_consumed:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction cannot commit before nonce consumption"
            )
        if self._plans.import_plan.receipt_to_insert is not None and (
            self._inserted_receipt != self._plans.import_plan.receipt_to_insert
            or not self._cursor_written
        ):
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction cannot commit before receipt and cursor persistence"
            )
        commit = _require_async_callable(getattr(self._session, "commit", None), label="receiver session commit")
        try:
            await commit()
        except Exception as exc:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction commit failed"
            ) from exc
        # A successful database commit cannot safely be followed by a later
        # coordinator rollback, even if cleanup reports an infrastructure
        # error.  Mark terminal before best-effort scope/session cleanup.
        self._terminal = True
        await self._close_scope_and_session()

    async def rollback(self) -> None:
        """Roll back one failed/replay transaction and always close its session."""

        if self._closed:
            return
        if self._terminal:
            # Commit succeeded.  The coordinator may call rollback only while
            # handling a later cleanup exception; never pretend it can undo a
            # committed Object receipt/nonce/cursor transaction.
            return
        self._terminal = True
        rollback_error: BaseException | None = None
        rollback = getattr(self._session, "rollback", None)
        try:
            if not callable(rollback):
                raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                    "receiver session rollback is unavailable"
                )
            value = rollback()
            if not inspect.isawaitable(value):
                raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                    "receiver session rollback is not asynchronous"
                )
            await value
        except BaseException as exc:
            rollback_error = exc
        try:
            await self._close_scope_and_session()
        except BaseException as close_error:
            if rollback_error is not None:
                raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                    "authorized receiver rollback and cleanup both failed"
                ) from close_error
            raise
        if rollback_error is not None:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver transaction rollback failed"
            ) from rollback_error


class SqlAlchemyAuthorizedObjectDeltaReceiverTransactionAdapter:
    """Factory-backed concrete implementation of the receiver transaction protocol.

    ``session_factory`` is deliberately the only database dependency.  It is
    expected to return a brand-new SQLAlchemy ``AsyncSession`` synchronously,
    as SQLAlchemy's session-factory callable does.  This adapter does not
    import application configuration or construct engines/sessions globally.
    """

    contract_name = AUTHORIZED_OBJECT_DELTA_RECEIVER_TRANSACTION_CONTRACT

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        payload_admission: AuthorizedObjectDeltaReceiverPayload,
    ) -> None:
        if not callable(session_factory):
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver session factory is invalid"
            )
        self._session_factory = session_factory
        self._payload_admission = payload_admission

    async def begin_authorized_object_delta_receiver_transaction(
        self,
        *,
        authorization: AuthorizedObjectDeltaReceiverDelivery,
    ) -> SqlAlchemyAuthorizedObjectDeltaReceiverTransaction:
        """Create one fresh session and hold its authorized scope until terminal."""

        try:
            verified = validate_authorized_object_delta_receiver_delivery(authorization)
            admitted = require_authorized_object_delta_receiver_payload(self._payload_admission)
        except (ObjectDeltaReceiverApplyScopeError, ObjectDeltaReceiverPayloadAdmissionError) as exc:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver delivery or payload is invalid before session creation"
            ) from exc
        if admitted.authorization is not verified:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver payload does not match the requested delivery"
            )
        try:
            session = self._session_factory()
        except Exception as exc:
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver session factory failed"
            ) from exc
        if inspect.isawaitable(session):
            raise SqlAlchemyAuthorizedObjectDeltaReceiverTransactionError(
                "authorized receiver session factory must return AsyncSession synchronously"
            )
        scope = authorized_object_delta_receiver_apply_scope(session, authorization=verified)
        try:
            await scope.__aenter__()
        except BaseException:
            try:
                await _await_session_close(session)
            except BaseException:
                pass
            raise
        return SqlAlchemyAuthorizedObjectDeltaReceiverTransaction(
            session=session,
            authorization=verified,
            payload_admission=admitted,
            scope=scope,
        )
