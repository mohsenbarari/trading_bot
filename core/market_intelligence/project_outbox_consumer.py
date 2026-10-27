"""Explicit, idempotent delivery of product-market outbox rows.

This module deliberately does *not* register a background task.  A later
runtime owner may call :class:`ProjectMarketOutboxConsumer` after configuring a
protected local Market Store path.  Offer and trade commits remain independent
from both PostgreSQL delivery retries and local SQLite availability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from models.coin_intelligence_market_outbox import CoinIntelligenceMarketOutbox

from .market_contracts import MarketObservation, derive_event_key
from .market_store import connect_market_store, initialize_market_store, upsert_observation


PROJECT_OUTBOX_CONSUMER_VERSION = "project-outbox-consumer-v1"
PROJECT_OUTBOX_SOURCE_CODE = "PROJECT_MARKET"
DEFAULT_LEASE_SECONDS = 60
MAX_RETRY_ATTEMPTS = 8
MAX_RETRY_DELAY_SECONDS = 300
_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,96}$")


class ProjectOutboxConsumerError(RuntimeError):
    """A stable, privacy-safe failure while projecting a product event."""


@dataclass(frozen=True, slots=True)
class ClaimedProjectMarketOutbox:
    """Immutable claim data, safe to retain after the SQLAlchemy commit."""

    id: int
    idempotency_key: str
    event_kind: str
    occurred_at_utc: datetime
    payload: Mapping[str, Any]
    model_eligible: bool
    attempts: int
    lease_token: str


@dataclass(frozen=True, slots=True)
class ProjectOutboxConsumeResult:
    """Outcome of one explicit delivery attempt; never exposes source text."""

    status: str
    outbox_id: int | None = None
    retry_at_utc: datetime | None = None
    error_code: str | None = None


def _utc(value: datetime | None = None) -> datetime:
    candidate = value or datetime.now(timezone.utc)
    if candidate.tzinfo is None or candidate.utcoffset() is None:
        return candidate.replace(tzinfo=timezone.utc)
    return candidate.astimezone(timezone.utc)


def _safe_error_code(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SAFE_ERROR_CODE.fullmatch(normalized):
        return "delivery_failed"
    return normalized


def _retry_delay_seconds(attempts: int) -> int:
    return min(MAX_RETRY_DELAY_SECONDS, 2 ** min(max(1, attempts), 8))


def _claimable_condition(now: datetime):
    return or_(
        and_(
            CoinIntelligenceMarketOutbox.status == "PENDING",
            CoinIntelligenceMarketOutbox.available_at_utc <= now,
        ),
        and_(
            CoinIntelligenceMarketOutbox.status == "PROCESSING",
            CoinIntelligenceMarketOutbox.lease_expires_at_utc.is_not(None),
            CoinIntelligenceMarketOutbox.lease_expires_at_utc <= now,
        ),
    )


def claim_next_project_market_outbox(
    session: Session,
    *,
    now: datetime | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> ClaimedProjectMarketOutbox | None:
    """Claim one due row inside the caller's PostgreSQL transaction.

    ``skip_locked`` makes concurrent workers safe on PostgreSQL.  SQLite test
    engines simply serialize their transaction; correctness still comes from
    the lease token and the Market Store's opaque-key upsert.
    """

    current = _utc(now)
    duration = max(1, int(lease_seconds))
    row = session.scalar(
        select(CoinIntelligenceMarketOutbox)
        .where(_claimable_condition(current))
        .order_by(CoinIntelligenceMarketOutbox.available_at_utc, CoinIntelligenceMarketOutbox.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if row is None:
        return None
    token = uuid4().hex
    row.status = "PROCESSING"
    row.attempts = int(row.attempts or 0) + 1
    row.lease_token = token
    row.lease_expires_at_utc = current + timedelta(seconds=duration)
    row.last_error_code = None
    session.flush()
    return ClaimedProjectMarketOutbox(
        id=int(row.id),
        idempotency_key=str(row.idempotency_key),
        event_kind=str(row.event_kind),
        occurred_at_utc=_utc(row.occurred_at_utc),
        payload=dict(row.payload or {}),
        model_eligible=bool(row.model_eligible),
        attempts=int(row.attempts),
        lease_token=token,
    )


def _required_payload(payload: Mapping[str, Any], field: str) -> Any:
    value = payload.get(field)
    if value is None or value == "":
        raise ProjectOutboxConsumerError(f"project_outbox_payload_{field}_missing")
    return value


def observation_from_project_outbox(
    claim: ClaimedProjectMarketOutbox,
    *,
    available_at_utc: datetime | None = None,
) -> MarketObservation:
    """Convert a checked economic payload into the canonical fact contract."""

    payload = claim.payload
    if int(_required_payload(payload, "version")) != 1:
        raise ProjectOutboxConsumerError("project_outbox_payload_version_unsupported")
    if str(_required_payload(payload, "instrument")).upper() != "PROJECT_COMMODITY":
        raise ProjectOutboxConsumerError("project_outbox_payload_instrument_invalid")
    if str(_required_payload(payload, "event_type")).upper() not in {"OFFER", "TRADE"}:
        raise ProjectOutboxConsumerError("project_outbox_payload_event_type_invalid")
    if str(_required_payload(payload, "price_unit")).upper() != "PROJECT_THOUSAND_TOMAN":
        raise ProjectOutboxConsumerError("project_outbox_payload_price_unit_invalid")
    try:
        commodity_id = int(_required_payload(payload, "commodity_id"))
    except (TypeError, ValueError) as exc:
        raise ProjectOutboxConsumerError("project_outbox_payload_commodity_invalid") from exc
    if commodity_id <= 0:
        raise ProjectOutboxConsumerError("project_outbox_payload_commodity_invalid")
    current = _utc(available_at_utc)
    occurred_at = _utc(claim.occurred_at_utc)
    if current < occurred_at:
        current = occurred_at
    status = str(payload.get("status") or "UNKNOWN").strip().upper()
    return MarketObservation(
        event_key=derive_event_key("project-market-outbox-v1", claim.idempotency_key),
        source_code=PROJECT_OUTBOX_SOURCE_CODE,
        source_family="PROJECT",
        event_time_utc=occurred_at,
        available_at_utc=current,
        instrument="PROJECT_COMMODITY",
        market_label=(
            "PROJECT_TRADE" if str(payload["event_type"]).upper() == "TRADE" else "PROJECT_OFFER"
        ),
        settlement_term=str(_required_payload(payload, "settlement_term")).upper(),
        trade_form=str(_required_payload(payload, "trade_form")).upper(),
        event_type=str(payload["event_type"]).upper(),
        side=str(_required_payload(payload, "side")).upper(),
        price=_required_payload(payload, "price"),
        price_unit="PROJECT_THOUSAND_TOMAN",
        currency=str(payload.get("currency") or "IRT").upper(),
        quantity=_required_payload(payload, "quantity"),
        quantity_unit="COIN_COUNT",
        parse_confidence=1.0,
        parser_version=PROJECT_OUTBOX_CONSUMER_VERSION,
        quality_state="ELIGIBLE" if claim.model_eligible else "IGNORED",
        quality_policy_version="project-direct-v1",
        attributes={
            "product_commodity_id": commodity_id,
            "lifecycle_event": claim.event_kind,
            "product_status": status,
            "remaining_quantity": int(payload.get("remaining_quantity") or 0),
        },
    )


def write_project_outbox_observation(
    claim: ClaimedProjectMarketOutbox,
    *,
    market_store_path: Path | str,
    available_at_utc: datetime | None = None,
) -> int:
    """Write the local fact atomically; a later retry is an idempotent upsert."""

    # Validate the product payload before touching the runtime database path.
    # A malformed row must not create an empty SQLite artifact that looks like
    # a partially initialized Market Store.
    observation = observation_from_project_outbox(
        claim,
        available_at_utc=available_at_utc,
    )
    connection: sqlite3.Connection | None = None
    try:
        connection = connect_market_store(market_store_path)
        initialize_market_store(connection)
        with connection:
            return upsert_observation(connection, observation)
    finally:
        if connection is not None:
            connection.close()


def complete_project_market_outbox(
    session: Session,
    *,
    claim: ClaimedProjectMarketOutbox,
    completed_at_utc: datetime | None = None,
) -> bool:
    """Mark a row complete only if this consumer still owns its lease."""

    row = session.scalar(
        select(CoinIntelligenceMarketOutbox)
        .where(CoinIntelligenceMarketOutbox.id == claim.id)
        .with_for_update()
    )
    if (
        row is None
        or row.status != "PROCESSING"
        or row.lease_token != claim.lease_token
    ):
        return False
    row.status = "COMPLETE"
    row.completed_at_utc = _utc(completed_at_utc)
    row.lease_token = None
    row.lease_expires_at_utc = None
    row.last_error_code = None
    session.flush()
    return True


def fail_project_market_outbox(
    session: Session,
    *,
    claim: ClaimedProjectMarketOutbox,
    error_code: str,
    now: datetime | None = None,
) -> ProjectOutboxConsumeResult:
    """Return a failed delivery to a bounded retry queue or terminal state."""

    row = session.scalar(
        select(CoinIntelligenceMarketOutbox)
        .where(CoinIntelligenceMarketOutbox.id == claim.id)
        .with_for_update()
    )
    safe_code = _safe_error_code(error_code)
    if (
        row is None
        or row.status != "PROCESSING"
        or row.lease_token != claim.lease_token
    ):
        return ProjectOutboxConsumeResult(
            status="CLAIM_LOST",
            outbox_id=claim.id,
            error_code="claim_lost",
        )
    current = _utc(now)
    row.last_error_code = safe_code
    row.lease_token = None
    row.lease_expires_at_utc = None
    if safe_code.startswith("project_outbox_payload_") or int(row.attempts or 0) >= MAX_RETRY_ATTEMPTS:
        row.status = "FAILED"
        session.flush()
        return ProjectOutboxConsumeResult(
            status="FAILED",
            outbox_id=claim.id,
            error_code=safe_code,
        )
    retry_at = current + timedelta(seconds=_retry_delay_seconds(int(row.attempts or 0)))
    row.status = "PENDING"
    row.available_at_utc = retry_at
    session.flush()
    return ProjectOutboxConsumeResult(
        status="RETRY_PENDING",
        outbox_id=claim.id,
        retry_at_utc=retry_at,
        error_code=safe_code,
    )


class ProjectMarketOutboxConsumer:
    """A one-row, explicitly invoked bridge from PostgreSQL to local SQLite."""

    def __init__(
        self,
        *,
        market_store_path: Path | str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self.market_store_path = Path(market_store_path)
        self.lease_seconds = max(1, int(lease_seconds))

    def consume_one(
        self,
        session: Session,
        *,
        now: datetime | None = None,
    ) -> ProjectOutboxConsumeResult:
        """Deliver at most one due event; caller never needs raw event payload."""

        current = _utc(now)
        claim = claim_next_project_market_outbox(
            session,
            now=current,
            lease_seconds=self.lease_seconds,
        )
        if claim is None:
            return ProjectOutboxConsumeResult(status="NO_ROW")
        session.commit()
        try:
            write_project_outbox_observation(
                claim,
                market_store_path=self.market_store_path,
                available_at_utc=current,
            )
        except ProjectOutboxConsumerError as exc:
            result = fail_project_market_outbox(
                session,
                claim=claim,
                error_code=str(exc),
                now=current,
            )
            session.commit()
            return result
        except (OSError, sqlite3.Error):
            result = fail_project_market_outbox(
                session,
                claim=claim,
                error_code="market_store_write_failed",
                now=current,
            )
            session.commit()
            return result
        if not complete_project_market_outbox(
            session,
            claim=claim,
            completed_at_utc=current,
        ):
            session.rollback()
            return ProjectOutboxConsumeResult(
                status="CLAIM_LOST",
                outbox_id=claim.id,
                error_code="claim_lost",
            )
        session.commit()
        return ProjectOutboxConsumeResult(status="COMPLETE", outbox_id=claim.id)
