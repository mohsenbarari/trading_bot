"""Operational helpers for medium/long cross-server outage recovery."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import redis.asyncio as redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.enums import NotificationCategory, NotificationLevel
from core.redis import get_redis_client
from core.server_routing import current_server, normalize_server
from core.services.offer_expiry_service import (
    OfferExpiryCommand,
    OfferExpiryReason,
    OfferExpirySourceSurface,
    apply_offer_expiry,
)
from core.utils import utc_now_naive
from models.change_log import ChangeLog
from models.notification import Notification
from models.offer import Offer, OfferStatus

logger = logging.getLogger(__name__)

ACTIVE_PUBLICATION_GATE_KEY = "sync:recovery:active_publication_gate"
RECOVERY_OUTAGE_CLASSES = {"medium", "long"}


@dataclass(frozen=True)
class SyncRecoveryHealthSnapshot:
    unsynced_change_log_count: int
    outbound_queue: int
    retry_queue: int
    redis_ok: bool = True
    oldest_unsynced_at: Any | None = None

    @property
    def clean(self) -> bool:
        return (
            self.redis_ok
            and self.unsynced_change_log_count == 0
            and self.outbound_queue == 0
            and self.retry_queue == 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "redis_ok": self.redis_ok,
            "unsynced_change_log_count": self.unsynced_change_log_count,
            "oldest_unsynced_at": self.oldest_unsynced_at,
            "redis_queues": {
                "sync:outbound": self.outbound_queue,
                "sync:retry": self.retry_queue,
            },
        }


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_cutoff(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _normalize_outage_class(value: str) -> str:
    outage_class = str(value or "").strip().lower()
    if outage_class not in RECOVERY_OUTAGE_CLASSES:
        raise ValueError("outage_class must be 'medium' or 'long'")
    return outage_class


def _safe_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


async def _new_redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


async def _resolve_redis_client(redis_client: Any | None) -> tuple[Any, bool]:
    if redis_client is not None:
        return redis_client, False
    try:
        return get_redis_client(), False
    except Exception:
        return await _new_redis_client(), True


async def load_active_publication_gate(redis_client: Any | None = None) -> dict[str, Any]:
    client, should_close = await _resolve_redis_client(redis_client)
    try:
        raw = await client.get(ACTIVE_PUBLICATION_GATE_KEY)
    finally:
        if should_close:
            await client.aclose()

    if not raw:
        return {"enabled": False}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"enabled": True, "invalid_payload": True}
    if not isinstance(payload, dict):
        return {"enabled": True, "invalid_payload": True}
    return {"enabled": bool(payload.get("enabled", True)), **payload}


async def active_publication_is_gated(redis_client: Any | None = None) -> bool:
    if redis_client is None:
        try:
            redis_client = get_redis_client()
        except Exception:
            return False
    try:
        gate = await load_active_publication_gate(redis_client)
    except Exception as exc:
        logger.warning(
            "Could not read cross-server recovery active-publication gate",
            extra={
                "event": "sync_recovery.active_publication_gate_read_failed",
                "error_type": type(exc).__name__,
            },
        )
        return False
    return bool(gate.get("enabled"))


async def enable_active_publication_gate(
    *,
    outage_class: str,
    server_mode: str | None = None,
    note: str | None = None,
    cutoff: datetime | None = None,
    redis_client: Any | None = None,
) -> dict[str, Any]:
    mode = normalize_server(server_mode, current_server())
    payload = {
        "enabled": True,
        "server_mode": mode,
        "outage_class": _normalize_outage_class(outage_class),
        "enabled_at": _utc_iso_now(),
        "reason": "medium_long_outage_recovery",
    }
    if note:
        payload["note"] = str(note)
    if cutoff is not None:
        payload["cutoff"] = _normalize_cutoff(cutoff).isoformat()

    client, should_close = await _resolve_redis_client(redis_client)
    try:
        await client.set(ACTIVE_PUBLICATION_GATE_KEY, json.dumps(payload, sort_keys=True))
    finally:
        if should_close:
            await client.aclose()
    return payload


async def clear_active_publication_gate(redis_client: Any | None = None) -> bool:
    client, should_close = await _resolve_redis_client(redis_client)
    try:
        deleted = await client.delete(ACTIVE_PUBLICATION_GATE_KEY)
    finally:
        if should_close:
            await client.aclose()
    return bool(deleted)


async def collect_sync_recovery_health(
    db: AsyncSession,
    *,
    redis_client: Any | None = None,
) -> SyncRecoveryHealthSnapshot:
    result = await db.execute(
        select(func.count(ChangeLog.id), func.min(ChangeLog.created_at)).where(ChangeLog.synced == False)
    )
    unsynced_count, oldest_unsynced_at = result.one()

    outbound_queue = 0
    retry_queue = 0
    redis_ok = True
    client, should_close = await _resolve_redis_client(redis_client)
    try:
        outbound_queue = _safe_int(await client.llen("sync:outbound"))
        retry_queue = _safe_int(await client.llen("sync:retry"))
    except Exception:
        redis_ok = False
    finally:
        if should_close:
            await client.aclose()

    return SyncRecoveryHealthSnapshot(
        unsynced_change_log_count=_safe_int(unsynced_count),
        oldest_unsynced_at=oldest_unsynced_at,
        outbound_queue=outbound_queue,
        retry_queue=retry_queue,
        redis_ok=redis_ok,
    )


async def count_recovery_active_offer_candidates(
    db: AsyncSession,
    *,
    server_mode: str,
    cutoff: datetime,
) -> int:
    result = await db.execute(
        select(func.count(Offer.id)).where(
            Offer.status == OfferStatus.ACTIVE,
            Offer.home_server == server_mode,
            Offer.created_at <= cutoff,
        )
    )
    try:
        return _safe_int(result.scalar())
    except AttributeError:
        row = result.one()
        return _safe_int(row[0])


async def load_recovery_active_offer_candidates(
    db: AsyncSession,
    *,
    server_mode: str,
    cutoff: datetime,
    limit: int = 100,
) -> list[Offer]:
    stmt = (
        select(Offer)
        .where(
            Offer.status == OfferStatus.ACTIVE,
            Offer.home_server == server_mode,
            Offer.created_at <= cutoff,
        )
        .order_by(Offer.created_at.asc(), Offer.id.asc())
        .limit(max(int(limit or 1), 1))
        .with_for_update(skip_locked=True)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def _candidate_payload(offer: Any) -> dict[str, Any]:
    return {
        "offer_id": getattr(offer, "id", None),
        "offer_public_id": getattr(offer, "offer_public_id", None),
        "user_id": getattr(offer, "user_id", None),
        "created_at": getattr(offer, "created_at", None),
        "home_server": getattr(offer, "home_server", None),
        "status": getattr(getattr(offer, "status", None), "value", getattr(offer, "status", None)),
    }


def _owner_notification_message(offer: Any) -> str:
    offer_public_id = getattr(offer, "offer_public_id", None) or getattr(offer, "id", "")
    return (
        "لفظ فعال شما به دلیل بازیابی ارتباط دو سرور و جلوگیری از انتشار آفر قدیمی "
        f"به صورت سیستمی منقضی شد. شناسه آفر: {offer_public_id}"
    )


def _add_owner_notifications(db: AsyncSession, offers: Iterable[Any]) -> int:
    notified = 0
    for offer in offers:
        user_id = getattr(offer, "user_id", None)
        if not user_id:
            continue
        db.add(
            Notification(
                user_id=user_id,
                message=_owner_notification_message(offer),
                is_read=False,
                level=NotificationLevel.WARNING,
                category=NotificationCategory.SYSTEM,
            )
        )
        notified += 1
    return notified


async def finalize_outage_recovery(
    db: AsyncSession,
    *,
    outage_class: str,
    cutoff: datetime,
    server_mode: str | None = None,
    dry_run: bool = True,
    limit: int = 100,
    redis_client: Any | None = None,
    health_snapshot: SyncRecoveryHealthSnapshot | None = None,
) -> dict[str, Any]:
    """Expire pre-recovery active home offers and keep publication gated until their final sync drains."""
    mode = normalize_server(server_mode, current_server())
    normalized_cutoff = _normalize_cutoff(cutoff)
    normalized_outage_class = _normalize_outage_class(outage_class)
    gate = await load_active_publication_gate(redis_client)
    health = health_snapshot or await collect_sync_recovery_health(db, redis_client=redis_client)

    total_candidates = await count_recovery_active_offer_candidates(
        db,
        server_mode=mode,
        cutoff=normalized_cutoff,
    )
    candidates = await load_recovery_active_offer_candidates(
        db,
        server_mode=mode,
        cutoff=normalized_cutoff,
        limit=limit,
    )
    candidate_payloads = [_candidate_payload(offer) for offer in candidates]

    base_report = {
        "server_mode": mode,
        "outage_class": normalized_outage_class,
        "dry_run": dry_run,
        "cutoff": normalized_cutoff.isoformat(),
        "active_publication_gate": gate,
        "health": health.to_dict(),
        "candidate_count": total_candidates,
        "loaded_candidate_count": len(candidates),
        "candidates": candidate_payloads,
    }

    if not health.clean:
        return {
            **base_report,
            "status": "gated",
            "expired_count": 0,
            "owner_notification_count": 0,
            "operator_action": "wait_for_sync_health_clean",
            "gate_cleared": False,
        }

    if dry_run:
        return {
            **base_report,
            "status": "would_finalize" if candidates else "ready_to_clear_gate",
            "expired_count": 0,
            "owner_notification_count": 0,
            "operator_action": "rerun_with_repair" if candidates else "rerun_with_repair_to_clear_gate",
            "gate_cleared": False,
        }

    if not candidates:
        gate_cleared = await clear_active_publication_gate(redis_client)
        return {
            **base_report,
            "status": "recovered",
            "expired_count": 0,
            "owner_notification_count": 0,
            "operator_action": "active_publication_gate_cleared",
            "gate_cleared": gate_cleared,
        }

    expiry_time = utc_now_naive()
    command = OfferExpiryCommand(
        reason=OfferExpiryReason.RECOVERY_FINALIZATION,
        source_surface=OfferExpirySourceSurface.SYSTEM,
        source_server=mode,
        expired_by_user_id=None,
        expired_by_actor_user_id=None,
    )
    expired_offers = []
    for offer in candidates:
        apply_offer_expiry(offer, command, now=expiry_time, require_authority=False)
        expired_offers.append(offer)
    notification_count = _add_owner_notifications(db, expired_offers)
    await db.commit()

    more_candidates_remain = total_candidates > len(candidates)
    return {
        **base_report,
        "status": "finalized_pending_sync" if not more_candidates_remain else "partial_finalized_pending_sync",
        "expired_count": len(expired_offers),
        "owner_notification_count": notification_count,
        "expired_offers": [_candidate_payload(offer) for offer in expired_offers],
        "operator_action": (
            "rerun_after_expiry_sync_health_clean"
            if not more_candidates_remain
            else "rerun_to_process_remaining_candidates"
        ),
        "gate_cleared": False,
    }
