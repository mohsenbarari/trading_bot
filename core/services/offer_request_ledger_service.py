"""Append-only offer request ledger helpers."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.server_routing import current_server, normalize_server
from core.utils import utc_now
from models.offer_request import OfferRequest, OfferRequestSourceSurface, OfferRequestStatus


TERMINAL_OFFER_REQUEST_STATUSES = frozenset(
    {
        OfferRequestStatus.REJECTED_BUSINESS_RULE,
        OfferRequestStatus.REJECTED_OFFER_EXPIRED,
        OfferRequestStatus.REJECTED_LOT_UNAVAILABLE,
        OfferRequestStatus.REJECTED_CONFLICT,
        OfferRequestStatus.COMPLETED_TRADE,
        OfferRequestStatus.DUPLICATE_REPLAY,
        OfferRequestStatus.FAILED_INTERNAL,
    }
)


class OfferRequestLedgerError(ValueError):
    pass


class OfferRequestTerminalStateError(OfferRequestLedgerError):
    pass


@dataclass(frozen=True)
class OfferRequestLedgerCommand:
    local_offer_id: int | None
    offer_public_id: str
    requester_user_id: int | None
    actor_user_id: int | None
    request_source_surface: OfferRequestSourceSurface | str
    request_source_server: str
    requested_quantity: int
    request_home_server: str | None = None
    idempotency_key: str | None = None
    received_at: Any = None
    result_status: OfferRequestStatus | str = OfferRequestStatus.RECEIVED
    public_failure_code: str | None = None
    public_failure_message: str | None = None
    internal_failure_code: str | None = None
    internal_failure_context: Mapping[str, Any] | None = None
    resulting_trade_id: int | None = None
    customer_relation_id: int | None = None
    customer_owner_user_id: int | None = None
    customer_tier_snapshot: str | None = None
    customer_management_name_snapshot: str | None = None
    customer_commission_rate_snapshot: Decimal | str | float | None = None
    customer_commission_context: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class OfferRequestLedgerResult:
    ledger: OfferRequest
    duplicate_replay: bool = False


def normalize_offer_request_status(value: OfferRequestStatus | str) -> OfferRequestStatus:
    if isinstance(value, OfferRequestStatus):
        return value
    normalized = str(value or "").strip().lower()
    try:
        return OfferRequestStatus(normalized)
    except ValueError as exc:
        raise ValueError(f"unsupported offer request status: {value}") from exc


def normalize_offer_request_source_surface(value: OfferRequestSourceSurface | str) -> OfferRequestSourceSurface:
    if isinstance(value, OfferRequestSourceSurface):
        return value
    normalized = str(value or "").strip().lower()
    try:
        return OfferRequestSourceSurface(normalized)
    except ValueError as exc:
        raise ValueError(f"unsupported offer request source surface: {value}") from exc


def _decimal_or_none(value: Decimal | str | float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _normalized_idempotency_key(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


async def _load_existing_by_idempotency(
    db: AsyncSession,
    *,
    request_home_server: str,
    idempotency_key: str | None,
) -> OfferRequest | None:
    if not idempotency_key:
        return None
    result = await db.execute(
        select(OfferRequest).where(
            OfferRequest.request_home_server == request_home_server,
            OfferRequest.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


def apply_offer_request_decision(
    ledger: OfferRequest,
    *,
    result_status: OfferRequestStatus | str,
    decided_at=None,
    public_failure_code: str | None = None,
    public_failure_message: str | None = None,
    internal_failure_code: str | None = None,
    internal_failure_context: Mapping[str, Any] | None = None,
    resulting_trade_id: int | None = None,
) -> OfferRequest:
    new_status = normalize_offer_request_status(result_status)
    current_status = normalize_offer_request_status(getattr(ledger, "result_status", OfferRequestStatus.RECEIVED))
    if current_status in TERMINAL_OFFER_REQUEST_STATUSES and new_status != current_status:
        raise OfferRequestTerminalStateError("terminal offer request rows cannot change outcome")

    ledger.result_status = new_status
    if new_status in TERMINAL_OFFER_REQUEST_STATUSES and getattr(ledger, "decided_at", None) is None:
        ledger.decided_at = decided_at or utc_now()
    elif decided_at is not None:
        ledger.decided_at = decided_at

    if public_failure_code is not None:
        ledger.public_failure_code = public_failure_code
    if public_failure_message is not None:
        ledger.public_failure_message = public_failure_message
    if internal_failure_code is not None:
        ledger.internal_failure_code = internal_failure_code
    if internal_failure_context is not None:
        ledger.internal_failure_context = dict(internal_failure_context)
    if resulting_trade_id is not None:
        ledger.resulting_trade_id = resulting_trade_id
    return ledger


def customer_relation_snapshot(relation: object | None) -> dict[str, Any]:
    if relation is None:
        return {
            "customer_relation_id": None,
            "customer_owner_user_id": None,
            "customer_tier_snapshot": None,
            "customer_management_name_snapshot": None,
            "customer_commission_rate_snapshot": None,
            "customer_commission_context": None,
        }
    tier = getattr(relation, "customer_tier", None)
    return {
        "customer_relation_id": getattr(relation, "id", None),
        "customer_owner_user_id": getattr(relation, "owner_user_id", None),
        "customer_tier_snapshot": getattr(tier, "value", tier),
        "customer_management_name_snapshot": getattr(relation, "management_name", None),
        "customer_commission_rate_snapshot": getattr(relation, "commission_rate", None),
        "customer_commission_context": {
            "min_trade_quantity": getattr(relation, "min_trade_quantity", None),
            "max_trade_quantity": getattr(relation, "max_trade_quantity", None),
            "max_daily_trades": getattr(relation, "max_daily_trades", None),
            "max_daily_commodity_volume": getattr(relation, "max_daily_commodity_volume", None),
        },
    }


async def create_offer_request_ledger_entry(
    db: AsyncSession,
    command: OfferRequestLedgerCommand,
    *,
    flush: bool = False,
) -> OfferRequestLedgerResult:
    offer_public_id = (command.offer_public_id or "").strip()
    if not offer_public_id:
        raise ValueError("offer_public_id is required for offer request ledger")
    if command.requested_quantity <= 0:
        raise ValueError("requested_quantity must be positive")

    request_home_server = normalize_server(command.request_home_server, current_server())
    idempotency_key = _normalized_idempotency_key(command.idempotency_key)
    existing = await _load_existing_by_idempotency(
        db,
        request_home_server=request_home_server,
        idempotency_key=idempotency_key,
    )
    if existing is not None:
        return OfferRequestLedgerResult(ledger=existing, duplicate_replay=True)

    status = normalize_offer_request_status(command.result_status)
    source_surface = normalize_offer_request_source_surface(command.request_source_surface)
    ledger_kwargs = {
        "request_home_server": request_home_server,
        "local_offer_id": command.local_offer_id,
        "offer_public_id": offer_public_id,
        "requester_user_id": command.requester_user_id,
        "actor_user_id": command.actor_user_id,
        "request_source_surface": source_surface,
        "request_source_server": normalize_server(command.request_source_server, current_server()),
        "requested_quantity": command.requested_quantity,
        "idempotency_key": idempotency_key,
        "result_status": status,
        "public_failure_code": command.public_failure_code,
        "public_failure_message": command.public_failure_message,
        "internal_failure_code": command.internal_failure_code,
        "internal_failure_context": dict(command.internal_failure_context) if command.internal_failure_context else None,
        "resulting_trade_id": command.resulting_trade_id,
        "customer_relation_id": command.customer_relation_id,
        "customer_owner_user_id": command.customer_owner_user_id,
        "customer_tier_snapshot": command.customer_tier_snapshot,
        "customer_management_name_snapshot": command.customer_management_name_snapshot,
        "customer_commission_rate_snapshot": _decimal_or_none(command.customer_commission_rate_snapshot),
        "customer_commission_context": dict(command.customer_commission_context) if command.customer_commission_context else None,
    }
    if command.received_at is not None:
        ledger_kwargs["received_at"] = command.received_at
    ledger = OfferRequest(**ledger_kwargs)
    if status in TERMINAL_OFFER_REQUEST_STATUSES:
        ledger.decided_at = utc_now()

    db.add(ledger)
    if flush:
        await db.flush()
    return OfferRequestLedgerResult(ledger=ledger, duplicate_replay=False)


def build_offer_request_history_query(
    *,
    offer_public_id: str,
    limit: int = 50,
    offset: int = 0,
):
    public_id = (offer_public_id or "").strip()
    if not public_id:
        raise ValueError("offer_public_id is required")
    safe_limit = min(max(int(limit or 50), 1), 100)
    safe_offset = max(int(offset or 0), 0)
    return (
        select(OfferRequest)
        .where(OfferRequest.offer_public_id == public_id)
        .order_by(OfferRequest.received_at.desc(), OfferRequest.id.desc())
        .limit(safe_limit)
        .offset(safe_offset)
    )
