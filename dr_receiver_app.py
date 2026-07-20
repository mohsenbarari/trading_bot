"""Minimal private DR ingress process with projection-only database authority."""

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.routers import dr_sync
from core.config import settings
from core.db import get_dr_projection_db, verify_three_site_database_role_bindings
from core.dr_sync_auth import DrSyncAuthError, parse_pairwise_keys
from core.dr_event_protocol import transport_peers
from core.runtime_identity import resolve_runtime_identity


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await verify_three_site_database_role_bindings()
    yield


app = FastAPI(
    title="Trading Bot Private DR Receiver",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


health = APIRouter()


@health.get("/live")
async def live():
    return {"status": "alive", "service": "dr-receiver"}


@health.get("/ready")
async def ready(db: AsyncSession = Depends(get_dr_projection_db)):
    reasons: list[str] = []
    identity = None
    if not (
        settings.three_site_dr_enabled
        and settings.dr_event_protocol_enabled
        and settings.dr_event_protocol_strict
    ):
        reasons.append("dr_event_protocol_not_strict")
    try:
        identity = resolve_runtime_identity(settings)
        keys = parse_pairwise_keys(settings.dr_sync_pairwise_keys_json)
        incoming = {
            (key.source_site, key.destination_site)
            for key in keys.values()
            if key.destination_site == identity.physical_site
        }
        expected_incoming = {
            (peer, identity.physical_site)
            for peer in transport_peers(identity.physical_site)
        }
        if incoming != expected_incoming:
            reasons.append("dr_inbound_key_topology_incomplete")
    except (RuntimeError, DrSyncAuthError, ValueError):
        reasons.append("dr_receiver_identity_or_keys_invalid")
    expected_revision = str(settings.origin_expected_migration_revision or "").strip()
    if not expected_revision:
        reasons.append("expected_migration_revision_missing")
    try:
        row = (
            await db.execute(
                text(
                    "SELECT session_user AS database_user, runtime.physical_site, "
                    "runtime.enforcement_enabled, "
                    "EXISTS (SELECT 1 FROM dr_projection_service_roles service_role "
                    "WHERE service_role.physical_site=runtime.physical_site "
                    "AND service_role.service_scope='receiver' "
                    "AND service_role.database_role=session_user) AS receiver_role_bound, "
                    "(SELECT version_num FROM alembic_version) AS migration_revision, "
                    "has_table_privilege(session_user, 'dr_replay_nonces', 'SELECT,INSERT') "
                    "AS nonce_privilege, "
                    "has_table_privilege(session_user, 'dr_events', 'SELECT,INSERT') "
                    "AS event_privilege, "
                    "has_table_privilege(session_user, 'dr_event_receipts', 'SELECT,INSERT,UPDATE') "
                    "AS receipt_privilege "
                    "FROM dr_database_runtime runtime WHERE runtime.singleton_id = 1"
                )
            )
        ).mappings().one_or_none()
        if row is None or row["enforcement_enabled"] is not True:
            reasons.append("dr_database_fencing_unavailable")
        else:
            if identity is None or row["physical_site"] != identity.physical_site:
                reasons.append("dr_database_site_mismatch")
            if row["receiver_role_bound"] is not True:
                reasons.append("dr_receiver_role_mismatch")
            if expected_revision and row["migration_revision"] != expected_revision:
                reasons.append("migration_revision_mismatch")
            if not all(
                row[name]
                for name in ("nonce_privilege", "event_privilege", "receipt_privilege")
            ):
                reasons.append("dr_receiver_privilege_incomplete")
    except Exception:
        reasons.append("dr_database_readiness_unavailable")
    reasons = list(dict.fromkeys(reasons))
    payload = {
        "status": "ready" if not reasons else "unready",
        "service": "dr-receiver",
        "physical_site": identity.physical_site if identity is not None else None,
        "reasons": reasons,
    }
    return JSONResponse(payload, status_code=200 if not reasons else 503)


app.include_router(health, prefix="/health")
app.include_router(dr_sync.router, prefix="/api/dr-sync", tags=["DR Sync"])
