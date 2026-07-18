"""Durable Iran-reachable witness state machine for the global WebApp writer term."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.runtime_sites import WEBAPP_SITES
from core.writer_witness_contract import sign_witness_lease_proof
from models.webapp_writer_state import (
    WebappWriterWitnessReceipt,
    WebappWriterWitnessState,
)


ACTION_ACQUIRE = "acquire"
ACTION_RENEW = "renew"
ACTION_DRAIN = "drain"
WITNESS_ACTIONS = frozenset({ACTION_ACQUIRE, ACTION_RENEW, ACTION_DRAIN})
WITNESS_COMMAND_VERSION = 1


class WriterWitnessError(RuntimeError):
    """Raised when a witness request is stale, ambiguous, or unsafe."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "writer_witness_rejected",
        replayed: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.replayed = replayed


class WriterWitnessCampaignExpiredError(WriterWitnessError):
    """Raised before any receipt/state mutation when campaign authority expired."""

    def __init__(self) -> None:
        super().__init__(
            "writer witness campaign credential has expired",
            code="witness_campaign_expired",
        )


@dataclass(frozen=True)
class WitnessStateSnapshot:
    holder_site: str | None
    writer_epoch: int
    lease_id: str | None
    lease_status: str
    issued_at: datetime | None
    expires_at: datetime | None
    transition_id: str


@dataclass(frozen=True)
class WitnessTransitionResult:
    state: WitnessStateSnapshot
    proof: dict[str, Any] | None
    replayed: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "contract_version": WITNESS_COMMAND_VERSION,
            "accepted": True,
            "state": {
                "holder_site": self.state.holder_site,
                "writer_epoch": self.state.writer_epoch,
                "lease_id": self.state.lease_id,
                "lease_status": self.state.lease_status,
                "issued_at": _iso(self.state.issued_at),
                "expires_at": _iso(self.state.expires_at),
                "transition_id": self.state.transition_id,
            },
            "proof": self.proof,
        }


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def witness_state_snapshot(state: WebappWriterWitnessState) -> WitnessStateSnapshot:
    return WitnessStateSnapshot(
        holder_site=state.holder_site,
        writer_epoch=int(state.writer_epoch),
        lease_id=state.lease_id,
        lease_status=state.lease_status,
        issued_at=state.issued_at,
        expires_at=state.expires_at,
        transition_id=state.transition_id,
    )


def _result_from_payload(payload: dict[str, Any]) -> WitnessTransitionResult:
    if payload.get("contract_version") != WITNESS_COMMAND_VERSION:
        raise WriterWitnessError("stored witness receipt has an unsupported contract version")
    if payload.get("accepted", True) is False:
        error = payload.get("error") or {}
        raise WriterWitnessError(
            str(error.get("message") or "stored witness request was rejected"),
            code=str(error.get("code") or "writer_witness_rejected"),
            replayed=True,
        )
    state = payload["state"]
    return WitnessTransitionResult(
        state=WitnessStateSnapshot(
            holder_site=state["holder_site"],
            writer_epoch=int(state["writer_epoch"]),
            lease_id=state["lease_id"],
            lease_status=state["lease_status"],
            issued_at=_parse_iso(state["issued_at"]),
            expires_at=_parse_iso(state["expires_at"]),
            transition_id=state["transition_id"],
        ),
        proof=payload.get("proof"),
        replayed=True,
    )


async def load_witness_state(
    session: AsyncSession,
    *,
    for_update: bool = False,
) -> WebappWriterWitnessState:
    statement = select(WebappWriterWitnessState).where(
        WebappWriterWitnessState.authority == "webapp"
    )
    if for_update:
        statement = statement.with_for_update()
    state = (await session.execute(statement)).scalar_one_or_none()
    if state is None:
        raise WriterWitnessError("webapp_writer_witness_state row is missing")
    return state


async def load_witness_snapshot(session: AsyncSession) -> WitnessStateSnapshot:
    return witness_state_snapshot(await load_witness_state(session))


async def _database_now(session: AsyncSession) -> datetime:
    value = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
    return _utc(value)


def witness_command_request_hash(
    *,
    action: str,
    requester_site: str,
    expected_epoch: int,
    expected_lease_id: str | None,
    lease_duration_seconds: int,
    operator: str,
    reason: str,
) -> str:
    payload = {
        "contract_version": WITNESS_COMMAND_VERSION,
        "action": action,
        "requester_site": requester_site,
        "expected_epoch": int(expected_epoch),
        "expected_lease_id": expected_lease_id,
        "lease_duration_seconds": int(lease_duration_seconds),
        "operator": operator,
        "reason": reason,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalized_command_values(
    *,
    action: str,
    requester_site: str,
    expected_epoch: int,
    expected_lease_id: str | None,
    request_id: str,
    operator: str,
    reason: str,
    lease_duration_seconds: int,
) -> tuple[str, str, str, int, str]:
    if action not in WITNESS_ACTIONS:
        raise WriterWitnessError(f"unsupported witness action={action!r}")
    if requester_site not in WEBAPP_SITES:
        raise WriterWitnessError(f"unsupported requester_site={requester_site!r}")
    request_id = request_id.strip()
    operator = operator.strip()
    reason = reason.strip()
    if not request_id or len(request_id) > 64:
        raise WriterWitnessError("request_id is required and must be <= 64 characters")
    if not operator or not reason:
        raise WriterWitnessError("operator and reason are mandatory")
    duration = int(lease_duration_seconds)
    if duration < 30 or duration > 3600:
        raise WriterWitnessError("lease duration must be between 30 and 3600 seconds")
    # Normalize the integer here so request hashing cannot disagree with the
    # state comparison later in the transaction.
    epoch = int(expected_epoch)
    return request_id, operator, reason, duration, str(epoch)


async def persist_witness_rejection(
    session: AsyncSession,
    *,
    action: str,
    requester_site: str,
    expected_epoch: int,
    expected_lease_id: str | None,
    request_id: str,
    operator: str,
    reason: str,
    lease_duration_seconds: int,
    error: WriterWitnessError,
) -> WriterWitnessError:
    """Persist an authenticated state-dependent rejection as a one-shot command.

    The caller must commit this transaction. Expected transition rejections do
    not invalidate a SQLAlchemy transaction and occur before state mutation, so
    recording the negative result closes the delayed-replay window without
    altering witness ownership.
    """

    request_id, operator, reason, duration, _ = _normalized_command_values(
        action=action,
        requester_site=requester_site,
        expected_epoch=expected_epoch,
        expected_lease_id=expected_lease_id,
        request_id=request_id,
        operator=operator,
        reason=reason,
        lease_duration_seconds=lease_duration_seconds,
    )
    request_hash = witness_command_request_hash(
        action=action,
        requester_site=requester_site,
        expected_epoch=expected_epoch,
        expected_lease_id=expected_lease_id,
        lease_duration_seconds=duration,
        operator=operator,
        reason=reason,
    )
    existing_receipt = await session.get(WebappWriterWitnessReceipt, request_id)
    if existing_receipt is not None:
        if existing_receipt.request_hash != request_hash:
            return WriterWitnessError(
                "request_id was already used with different parameters",
                code="request_id_reused",
                replayed=True,
            )
        try:
            _result_from_payload(json.loads(existing_receipt.response_json))
        except WriterWitnessError as stored:
            return stored
        return WriterWitnessError(
            "request_id already completed successfully",
            code="request_already_succeeded",
            replayed=True,
        )

    transition_id = f"rejected:{uuid4()}"
    response_json = json.dumps(
        {
            "contract_version": WITNESS_COMMAND_VERSION,
            "accepted": False,
            "error": {"code": error.code, "message": str(error)},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    session.add(
        WebappWriterWitnessReceipt(
            request_id=request_id,
            request_hash=request_hash,
            action=action,
            transition_id=transition_id,
            response_json=response_json,
        )
    )
    await session.flush()
    return error


async def transition_witness_state(
    session: AsyncSession,
    *,
    action: str,
    requester_site: str,
    expected_epoch: int,
    expected_lease_id: str | None,
    request_id: str,
    operator: str,
    reason: str,
    private_key_base64: str | None,
    lease_duration_seconds: int = 180,
    now: datetime | None = None,
    authorization_not_after: datetime | None = None,
) -> WitnessTransitionResult:
    request_id, operator, reason, duration, _ = _normalized_command_values(
        action=action,
        requester_site=requester_site,
        expected_epoch=expected_epoch,
        expected_lease_id=expected_lease_id,
        request_id=request_id,
        operator=operator,
        reason=reason,
        lease_duration_seconds=lease_duration_seconds,
    )

    current = _utc(now) if now is not None else await _database_now(session)
    if (
        authorization_not_after is not None
        and current >= _utc(authorization_not_after)
    ):
        # This check deliberately precedes receipt lookup and state locking.
        # An expired campaign credential has no mutation authority and must not
        # leave a durable receipt that could be replayed as an authorization.
        raise WriterWitnessCampaignExpiredError()

    request_hash = witness_command_request_hash(
        action=action,
        requester_site=requester_site,
        expected_epoch=expected_epoch,
        expected_lease_id=expected_lease_id,
        lease_duration_seconds=duration,
        operator=operator,
        reason=reason,
    )
    existing_receipt = await session.get(WebappWriterWitnessReceipt, request_id)
    if existing_receipt is not None:
        if existing_receipt.request_hash != request_hash:
            raise WriterWitnessError("request_id was already used with different parameters")
        return _result_from_payload(json.loads(existing_receipt.response_json))

    state = await load_witness_state(session, for_update=True)
    # A concurrent exact retry may have committed while this transaction was
    # waiting for the singleton row lock. Recheck the receipt under the lock.
    existing_receipt = await session.get(WebappWriterWitnessReceipt, request_id)
    if existing_receipt is not None:
        if existing_receipt.request_hash != request_hash:
            raise WriterWitnessError("request_id was already used with different parameters")
        return _result_from_payload(json.loads(existing_receipt.response_json))
    current_epoch = int(state.writer_epoch)
    if current_epoch != int(expected_epoch):
        raise WriterWitnessError(
            f"stale witness epoch: current={current_epoch} expected={expected_epoch}"
        )
    if state.lease_id != expected_lease_id:
        raise WriterWitnessError(
            f"stale witness lease: current={state.lease_id!r} expected={expected_lease_id!r}"
        )

    transition_id = str(uuid4())
    proof: dict[str, Any] | None = None
    lease_is_live = state.expires_at is not None and _utc(state.expires_at) > current

    if action == ACTION_ACQUIRE:
        if lease_is_live:
            raise WriterWitnessError("a live witness lease already exists; acquisition is blocked")
        if not private_key_base64:
            raise WriterWitnessError("witness private key is required to acquire a lease")
        state.holder_site = requester_site
        state.writer_epoch = current_epoch + 1
        state.lease_id = str(uuid4())
        state.lease_status = "leased"
        state.issued_at = current
        state.expires_at = current + timedelta(seconds=duration)
    elif action == ACTION_RENEW:
        if not lease_is_live:
            raise WriterWitnessError("expired witness leases cannot be renewed; acquire a new term")
        if state.lease_status != "leased":
            raise WriterWitnessError("a draining witness lease cannot be renewed")
        if state.holder_site != requester_site:
            raise WriterWitnessError("only the current lease holder may renew")
        if not private_key_base64:
            raise WriterWitnessError("witness private key is required to renew a lease")
        state.issued_at = current
        state.expires_at = current + timedelta(seconds=duration)
    else:
        if not lease_is_live:
            raise WriterWitnessError("an expired witness lease cannot enter drain")
        if state.lease_status != "leased":
            raise WriterWitnessError("witness lease is already draining")
        if state.holder_site != requester_site:
            raise WriterWitnessError("only the current lease holder may enter drain")
        state.lease_status = "draining"

    state.transition_id = transition_id
    state.updated_by = operator
    state.reason = reason
    snapshot = witness_state_snapshot(state)
    if action in {ACTION_ACQUIRE, ACTION_RENEW}:
        proof = sign_witness_lease_proof(
            holder_site=snapshot.holder_site or requester_site,
            writer_epoch=snapshot.writer_epoch,
            lease_id=snapshot.lease_id or "",
            issued_at=snapshot.issued_at or current,
            expires_at=snapshot.expires_at or current,
            witness_transition_id=transition_id,
            private_key_base64=private_key_base64 or "",
        )
    result = WitnessTransitionResult(state=snapshot, proof=proof)
    response_json = json.dumps(
        result.as_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    session.add(
        WebappWriterWitnessReceipt(
            request_id=request_id,
            request_hash=request_hash,
            action=action,
            transition_id=transition_id,
            response_json=response_json,
        )
    )
    await session.flush()
    return result
