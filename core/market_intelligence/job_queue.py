"""Local PostgreSQL durable queue for non-authoritative Shadow work."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Awaitable, Callable
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from core.config import settings
from core.db import AsyncSessionLocal
from models.coin_intelligence_shadow import CoinIntelligenceShadowJob


JobHandler = Callable[[CoinIntelligenceShadowJob], Awaitable[None]]


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None or result.utcoffset() is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _key(kind: str, local_id: int) -> str:
    # One committed business row maps to one replay-safe Shadow job.
    material = json.dumps(
        [str(kind).upper(), int(local_id)],
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def enqueue_project_job(
    *,
    kind: str,
    local_id: int,
    requested_at_utc: datetime,
    session_factory=AsyncSessionLocal,
) -> str:
    normalized_kind = f"PROJECT_{str(kind).upper()}"
    if normalized_kind not in {"PROJECT_OFFER", "PROJECT_TRADE"}:
        raise ValueError("unsupported coin intelligence job kind")
    requested_at = _utc(requested_at_utc)
    key = _key(normalized_kind, int(local_id))
    async with session_factory() as session:
        existing = await session.scalar(
            select(CoinIntelligenceShadowJob.id).where(
                CoinIntelligenceShadowJob.idempotency_key == key
            )
        )
        if existing is not None:
            return str(existing)
        job_id = str(uuid4())
        session.add(
            CoinIntelligenceShadowJob(
                id=job_id,
                idempotency_key=key,
                job_kind=normalized_kind,
                local_id=int(local_id),
                payload={},
                requested_at_utc=requested_at,
                max_attempts=int(
                    settings.coin_intelligence_shadow_worker_max_attempts
                ),
                available_at=datetime.now(timezone.utc),
            )
        )
        try:
            await session.commit()
            return job_id
        except IntegrityError:
            await session.rollback()
            winner = await session.scalar(
                select(CoinIntelligenceShadowJob.id).where(
                    CoinIntelligenceShadowJob.idempotency_key == key
                )
            )
            if winner is None:
                raise
            return str(winner)


async def claim_job(
    *,
    worker_token: str,
    now_utc: datetime | None = None,
    session_factory=AsyncSessionLocal,
) -> CoinIntelligenceShadowJob | None:
    now = _utc(now_utc)
    lease_expires = now + timedelta(
        seconds=int(settings.coin_intelligence_shadow_worker_lease_seconds)
    )
    async with session_factory() as session:
        row = await session.scalar(
            select(CoinIntelligenceShadowJob)
            .where(
                CoinIntelligenceShadowJob.available_at <= now,
                or_(
                    CoinIntelligenceShadowJob.status == "PENDING",
                    and_(
                        CoinIntelligenceShadowJob.status == "PROCESSING",
                        CoinIntelligenceShadowJob.lease_expires_at < now,
                    ),
                ),
            )
            .order_by(
                CoinIntelligenceShadowJob.available_at.asc(),
                CoinIntelligenceShadowJob.created_at.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        row.status = "PROCESSING"
        row.attempts = int(row.attempts or 0) + 1
        row.worker_token = str(worker_token)[:64]
        row.lease_expires_at = lease_expires
        row.error_code = None
        await session.commit()
        # Detach before the session closes. Only normalized queue data remains.
        session.expunge(row)
        return row


async def complete_job(
    job_id: str,
    *,
    worker_token: str,
    session_factory=AsyncSessionLocal,
) -> bool:
    async with session_factory() as session:
        result = await session.execute(
            update(CoinIntelligenceShadowJob)
            .where(
                CoinIntelligenceShadowJob.id == str(job_id),
                CoinIntelligenceShadowJob.status == "PROCESSING",
                CoinIntelligenceShadowJob.worker_token
                == str(worker_token)[:64],
            )
            .values(
                status="COMPLETE",
                lease_expires_at=None,
                worker_token=None,
                error_code=None,
            )
        )
        await session.commit()
        return bool(result.rowcount)


async def fail_job(
    job: CoinIntelligenceShadowJob,
    *,
    worker_token: str,
    error_code: str,
    now_utc: datetime | None = None,
    session_factory=AsyncSessionLocal,
) -> bool:
    now = _utc(now_utc)
    terminal = int(job.attempts or 0) >= int(job.max_attempts or 1)
    delay_seconds = min(60, 2 ** max(0, int(job.attempts or 1) - 1))
    async with session_factory() as session:
        result = await session.execute(
            update(CoinIntelligenceShadowJob)
            .where(
                CoinIntelligenceShadowJob.id == str(job.id),
                CoinIntelligenceShadowJob.status == "PROCESSING",
                CoinIntelligenceShadowJob.worker_token
                == str(worker_token)[:64],
            )
            .values(
                status="FAILED" if terminal else "PENDING",
                available_at=now + timedelta(seconds=delay_seconds),
                lease_expires_at=None,
                worker_token=None,
                error_code=str(error_code).upper()[:96],
            )
        )
        await session.commit()
        return bool(result.rowcount)


async def process_one_job(
    *,
    worker_token: str,
    handler: JobHandler,
    session_factory=AsyncSessionLocal,
) -> bool:
    job = await claim_job(
        worker_token=worker_token,
        session_factory=session_factory,
    )
    if job is None:
        return False
    try:
        await handler(job)
    except Exception as exc:
        await fail_job(
            job,
            worker_token=worker_token,
            error_code=type(exc).__name__,
            session_factory=session_factory,
        )
    else:
        await complete_job(
            str(job.id),
            worker_token=worker_token,
            session_factory=session_factory,
        )
    return True


async def run_worker(
    *,
    handler: JobHandler,
    once: bool = False,
) -> None:
    worker_token = uuid4().hex
    poll_seconds = float(
        settings.coin_intelligence_shadow_worker_poll_seconds
    )
    while True:
        processed = await process_one_job(
            worker_token=worker_token,
            handler=handler,
        )
        if once:
            return
        if not processed:
            await asyncio.sleep(poll_seconds)
