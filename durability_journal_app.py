"""Private Bot-FI receiver for opaque same-region journal coordination.

It deliberately has a dedicated database role and never imports the ordinary
DR receiver/router.  The process can persist ciphertext and resolve a 2PC
state machine, but cannot read or project WebApp business rows.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings
from core.dr_durability_journal import (
    DurabilityJournalError,
    JournalPrepare,
    acknowledgement_payload,
    parse_prepare,
    parse_resolution,
)
from core.dr_durability_journal_store import (
    commit_record,
    prepare_record,
    read_record,
    rollback_record,
)
from core.dr_sync_auth import DrSyncAuthError, parse_pairwise_keys, sign_acknowledgement, verify_request
from core.runtime_identity import resolve_runtime_identity
from core.runtime_sites import SITE_BOT_FI, SITE_WEBAPP_FI
from models.dr_event import DrSameRegionJournal


_engine = create_async_engine(
    settings.database_url,
    pool_size=max(1, int(settings.dr_auxiliary_db_pool_size)),
    max_overflow=max(0, int(settings.dr_auxiliary_db_max_overflow)),
    pool_pre_ping=settings.db_pool_pre_ping,
    pool_recycle=settings.db_pool_recycle_seconds,
    echo=False,
)
SessionLocal = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


def _record_to_prepare(row: DrSameRegionJournal) -> JournalPrepare:
    return JournalPrepare(
        origin_physical_site=row.origin_physical_site,
        writer_epoch=int(row.writer_epoch),
        transaction_id=row.transaction_id,
        transaction_hash=row.transaction_hash,
        release_sha=row.release_sha,
        encryption_key_id=row.encryption_key_id,
        event_ids=tuple(str(item) for item in row.event_ids or ()),
        event_hashes=tuple(str(item) for item in row.event_hashes or ()),
        nonce=row.nonce,
        ciphertext=row.ciphertext,
        ciphertext_hash=row.ciphertext_hash,
    )


def _authenticated(request: Request, body: bytes):  # noqa: ANN001
    keys = parse_pairwise_keys(settings.dr_same_region_journal_pairwise_keys_json)
    authenticated = verify_request(
        method=request.method,
        path=request.url.path,
        body=body,
        headers={key.lower(): value for key, value in request.headers.items()},
        keys=keys,
        expected_destination_site=SITE_BOT_FI,
        max_age_seconds=settings.dr_sync_request_max_age_seconds,
    )
    if authenticated.source_site != SITE_WEBAPP_FI:
        raise DurabilityJournalError("journal source is outside the approved FI path")
    return authenticated, keys[authenticated.key_id]


def _acknowledgement(*, row: DrSameRegionJournal, request_hash: str, secret: str) -> dict[str, Any]:
    unsigned = acknowledgement_payload(
        prepare=_record_to_prepare(row),
        state=row.state,
        request_hash=request_hash,
        prepared_transaction_gid=row.prepared_transaction_gid,
        resolved_at=row.resolved_at or row.prepared_at,
    )
    return {
        **unsigned,
        "acknowledgement_mac": sign_acknowledgement(payload=unsigned, secret=secret),
    }


async def _journal_readiness(session: AsyncSession) -> list[str]:
    reasons: list[str] = []
    if not settings.dr_same_region_journal_enabled:
        reasons.append("same_region_journal_disabled")
    try:
        identity = resolve_runtime_identity(settings)
        if identity.physical_site != SITE_BOT_FI or not identity.is_bot_site:
            reasons.append("same_region_journal_site_mismatch")
        keys = parse_pairwise_keys(settings.dr_same_region_journal_pairwise_keys_json)
        expected = {
            (SITE_WEBAPP_FI, SITE_BOT_FI),
        }
        observed = {(item.source_site, item.destination_site) for item in keys.values()}
        if observed != expected:
            reasons.append("same_region_journal_key_topology_invalid")
    except (RuntimeError, DrSyncAuthError, ValueError):
        reasons.append("same_region_journal_identity_or_key_invalid")
    try:
        row = (
            await session.execute(
                text(
                    "SELECT session_user AS database_role, "
                    "has_table_privilege(session_user, 'public.dr_same_region_journal', 'SELECT,INSERT,UPDATE') AS journal_privilege, "
                    "COALESCE(bool_and(CASE WHEN relation.relname = 'dr_same_region_journal' THEN true "
                    "ELSE NOT has_table_privilege(session_user, relation.oid, 'SELECT,INSERT,UPDATE,DELETE') END), false) AS closed_table_surface "
                    "FROM pg_class relation JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace "
                    "WHERE namespace.nspname='public' AND relation.relkind IN ('r','p')"
                )
            )
        ).mappings().one_or_none()
        if row is None or row["database_role"] != "bot_fi_journal":
            reasons.append("same_region_journal_database_role_mismatch")
        elif row["journal_privilege"] is not True or row["closed_table_surface"] is not True:
            reasons.append("same_region_journal_database_privilege_invalid")
    except Exception:
        reasons.append("same_region_journal_database_unavailable")
    return list(dict.fromkeys(reasons))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with SessionLocal() as session:
        reasons = await _journal_readiness(session)
    if reasons:
        raise RuntimeError("same_region_journal_unready:" + ",".join(reasons))
    yield
    await _engine.dispose()


app = FastAPI(
    title="Trading Bot Same-Region Journal Receiver",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/health/live")
async def live():
    return {"status": "alive", "service": "same-region-journal"}


@app.get("/health/ready")
async def ready():
    async with SessionLocal() as session:
        reasons = await _journal_readiness(session)
    return JSONResponse(
        {
            "status": "ready" if not reasons else "unready",
            "service": "same-region-journal",
            "physical_site": SITE_BOT_FI,
            "reasons": reasons,
        },
        status_code=200 if not reasons else 503,
    )


async def _body(request: Request, *, limit: int = 8 * 1024 * 1024) -> bytes:
    body = await request.body()
    if len(body) > limit:
        raise HTTPException(status_code=413, detail="journal request is too large")
    return body


@app.post("/api/dr-journal/v1/prepare")
async def prepare(request: Request):
    body = await _body(request)
    try:
        authenticated, key = _authenticated(request, body)
        payload = parse_prepare(body)
        async with SessionLocal() as session:
            row = await prepare_record(session, prepare=payload, request_hash=authenticated.request_hash)
            await session.commit()
            return _acknowledgement(row=row, request_hash=authenticated.request_hash, secret=key.secret)
    except (DrSyncAuthError, DurabilityJournalError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/dr-journal/v1/commit")
async def commit(request: Request):
    body = await _body(request, limit=64 * 1024)
    try:
        authenticated, key = _authenticated(request, body)
        resolution = parse_resolution(body, require_prepared_gid=True)
        async with SessionLocal() as session:
            row = await commit_record(session, resolution=resolution)
            await session.commit()
            return _acknowledgement(row=row, request_hash=authenticated.request_hash, secret=key.secret)
    except (DrSyncAuthError, DurabilityJournalError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/dr-journal/v1/rollback")
async def rollback(request: Request):
    body = await _body(request, limit=64 * 1024)
    try:
        authenticated, key = _authenticated(request, body)
        resolution = parse_resolution(body, require_prepared_gid=False)
        async with SessionLocal() as session:
            row = await rollback_record(session, resolution=resolution)
            await session.commit()
            return _acknowledgement(row=row, request_hash=authenticated.request_hash, secret=key.secret)
    except (DrSyncAuthError, DurabilityJournalError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/dr-journal/v1/status")
async def status(request: Request):
    body = await _body(request, limit=64 * 1024)
    try:
        authenticated, key = _authenticated(request, body)
        resolution = parse_resolution(body, require_prepared_gid=False)
        async with SessionLocal() as session:
            row = await read_record(session, resolution=resolution)
            if row is None:
                raise DurabilityJournalError("journal status references no record")
            acknowledgement = _acknowledgement(
                row=row,
                request_hash=authenticated.request_hash,
                secret=key.secret,
            )
            return acknowledgement
    except (DrSyncAuthError, DurabilityJournalError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
