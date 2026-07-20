"""Minimal private DR ingress process with projection-only database authority."""

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from api.routers import dr_sync
from core.db import verify_three_site_database_role_bindings


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
async def ready():
    return {"status": "ready", "service": "dr-receiver"}


app.include_router(health, prefix="/health")
app.include_router(dr_sync.router, prefix="/api/dr-sync", tags=["DR Sync"])
