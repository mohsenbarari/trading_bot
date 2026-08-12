"""Fast, durable-outbox acceleration for Iran-owned offer replication.

This module deliberately does *not* introduce a second business replication
protocol.  An Offer is still written once, its normal ``change_log`` row is
still the recovery source, and the peer still accepts it through the existing
signed ``/api/sync/receive`` contract.  The only difference is scheduling:
Iran-owned Offer rows are given an immediate, bounded delivery attempt so the
foreign Telegram publisher need not wait behind unrelated sync traffic.

Failure is safe by construction.  A failed or lost acknowledgement leaves the
same change-log row unsynced for the regular sync worker.  A duplicate request
is harmless because the existing source-sequence watermark and Offer public
identity are the receiver's idempotency fence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

import httpx
from sqlalchemy import or_, select

from core.config import settings
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, peer_server_url_for
from core.sync_metadata import deserialize_sync_data
from core.sync_transport import runtime_sync_tls_verify_setting
from core.utils import utc_now
from models.change_log import ChangeLog


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OfferPrioritySyncResult:
    attempted: bool
    delivered: bool
    skipped_reason: str | None = None
    status_code: int | None = None
    change_log_id: int | None = None


def _enabled() -> bool:
    return bool(getattr(settings, "offer_priority_sync_enabled", True))


def _timeout_seconds() -> float:
    try:
        return min(5.0, max(0.2, float(settings.offer_priority_sync_timeout_seconds)))
    except (TypeError, ValueError):
        return 2.0


def _max_change_age_seconds() -> float:
    try:
        return min(300.0, max(1.0, float(settings.offer_priority_sync_max_change_age_seconds)))
    except (TypeError, ValueError):
        return 45.0


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _is_iran_owned_offer_change(entry: ChangeLog, *, offer_public_id: str | None = None) -> bool:
    if entry.table_name != "offers" or entry.operation not in {"INSERT", "UPDATE"}:
        return False
    data = deserialize_sync_data(entry.data)
    if not isinstance(data, dict):
        return False
    if _normalized_text(data.get("home_server")).lower() != SERVER_IRAN:
        return False
    public_id = _normalized_text(data.get("offer_public_id"))
    return bool(public_id) and (offer_public_id is None or public_id == offer_public_id)


def _is_recent_committed_change(entry: ChangeLog) -> bool:
    """Fast lane is for live work, never a deploy/outage backlog drain."""

    timestamp = getattr(entry, "timestamp", None)
    if not isinstance(timestamp, datetime):
        return False
    committed_at = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=timezone.utc)
    age_seconds = (utc_now() - committed_at.astimezone(timezone.utc)).total_seconds()
    return 0.0 <= age_seconds <= _max_change_age_seconds()


async def load_latest_pending_offer_priority_sync_item(
    db,
    *,
    offer_public_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the newest committed Iran-owned Offer change awaiting delivery.

    Sending the latest version first is intentional.  If an earlier version is
    later delivered by normal sync, its source-sequence watermark makes it a
    no-op, which is safer than briefly publishing an already-terminal offer.
    """

    rows = list(
        (
            await db.execute(
                select(ChangeLog)
                .where(
                    ChangeLog.synced.is_(False),
                    ChangeLog.quarantined_at.is_(None),
                    or_(
                        ChangeLog.next_delivery_attempt_at.is_(None),
                        ChangeLog.next_delivery_attempt_at <= utc_now(),
                    ),
                    ChangeLog.table_name == "offers",
                    ChangeLog.operation.in_(("INSERT", "UPDATE")),
                )
                .order_by(ChangeLog.id.desc())
                .limit(128)
            )
        ).scalars()
    )
    for entry in rows:
        if not _is_iran_owned_offer_change(entry, offer_public_id=offer_public_id):
            continue
        if not _is_recent_committed_change(entry):
            continue
        # Import lazily: sync_worker owns the canonical metadata envelope and
        # importing it at module import time would create a worker/service
        # cycle.
        from core.sync_worker import change_log_entry_to_sync_item

        return change_log_entry_to_sync_item(entry)
    return None


async def dispatch_offer_priority_sync_once(
    db,
    *,
    offer_public_id: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> OfferPrioritySyncResult:
    """Make one bounded immediate delivery attempt for one committed Offer.

    This must be called strictly after the business transaction committed.  It
    never changes offer truth and never marks the durable outbox delivered
    unless the existing sync receiver acknowledged a complete success.
    """

    if not _enabled():
        return OfferPrioritySyncResult(False, False, "disabled")
    if current_server() != SERVER_IRAN:
        return OfferPrioritySyncResult(False, False, "not_iran_source")

    # The sync worker has no request-scoped session.  Keep its read/attempt
    # isolated from the generic worker transaction while allowing API callers
    # to reuse their already-committed session.  The server-role guard above
    # is deliberately before this branch so foreign workers add no database
    # polling overhead at all.
    if db is None:
        from core.db import AsyncSessionLocal

        async with AsyncSessionLocal() as owned_db:
            return await dispatch_offer_priority_sync_once(
                owned_db,
                offer_public_id=offer_public_id,
                client=client,
            )

    target_url = peer_server_url_for(SERVER_FOREIGN)
    api_key = _normalized_text(settings.sync_api_key)
    if not target_url or not api_key:
        return OfferPrioritySyncResult(False, False, "peer_unconfigured")

    item = await load_latest_pending_offer_priority_sync_item(
        db,
        offer_public_id=offer_public_id,
    )
    if item is None:
        return OfferPrioritySyncResult(False, False, "no_pending_offer_change")

    from core.sync_worker import mark_change_log_delivered, peer_response_is_success, send_sync_item

    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        verify=runtime_sync_tls_verify_setting(),
        timeout=_timeout_seconds(),
    )
    try:
        response = await send_sync_item(
            active_client,
            item,
            target_url.rstrip("/"),
            api_key,
            timeout_seconds=_timeout_seconds(),
        )
        if peer_response_is_success(response):
            marked = await mark_change_log_delivered(item)
            logger.info(
                "Priority offer sync delivered committed offer change",
                extra={
                    "event": "offer_priority_sync.delivered",
                    "offer_public_id": _normalized_text(
                        offer_public_id or (item.get("data") or {}).get("offer_public_id")
                    )[:40] or None,
                    "change_log_id": item.get("change_log_id"),
                    "marked_change_logs": marked,
                    "status_code": response.status_code,
                },
            )
            return OfferPrioritySyncResult(
                True,
                True,
                status_code=response.status_code,
                change_log_id=item.get("change_log_id"),
            )
        logger.warning(
            "Priority offer sync not acknowledged; durable sync will retry",
            extra={
                "event": "offer_priority_sync.not_acknowledged",
                "offer_public_id": _normalized_text(
                    offer_public_id or (item.get("data") or {}).get("offer_public_id")
                )[:40] or None,
                "change_log_id": item.get("change_log_id"),
                "status_code": getattr(response, "status_code", None),
            },
        )
        return OfferPrioritySyncResult(
            True,
            False,
            "peer_not_acknowledged",
            status_code=getattr(response, "status_code", None),
            change_log_id=item.get("change_log_id"),
        )
    except httpx.RequestError as exc:
        logger.info(
            "Priority offer sync unavailable; durable sync will retry",
            extra={
                "event": "offer_priority_sync.transport_deferred",
                "offer_public_id": _normalized_text(
                    offer_public_id or (item.get("data") or {}).get("offer_public_id")
                )[:40] or None,
                "change_log_id": item.get("change_log_id"),
                "error_class": type(exc).__name__,
            },
        )
        return OfferPrioritySyncResult(
            True,
            False,
            "transport_deferred",
            change_log_id=item.get("change_log_id"),
        )
    finally:
        if owns_client:
            await active_client.aclose()
