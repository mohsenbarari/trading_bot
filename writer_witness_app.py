"""Private, separately deployable control API for the WebApp writer witness."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import json
import logging
from pathlib import Path
import re
from typing import Any, Awaitable, Callable

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic_settings import BaseSettings
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.runtime_sites import AUTHORITY_WEBAPP, SITE_WEBAPP_IR
from core.writer_witness_auth import (
    WITNESS_STATUS_PATH,
    WITNESS_TRANSITION_PATH,
    WitnessAuthenticationError,
    WitnessClientCredential,
    verify_witness_request,
)
from core.writer_witness_contract import witness_timing_configuration_is_safe
from core.writer_witness_control import (
    WITNESS_ACTIONS,
    WITNESS_COMMAND_VERSION,
    WriterWitnessError,
    WriterWitnessCampaignExpiredError,
    load_witness_snapshot,
    persist_witness_rejection,
    transition_witness_state,
)


TRANSITION_PATH = WITNESS_TRANSITION_PATH
STATUS_PATH = WITNESS_STATUS_PATH
WITNESS_SCHEMA_VERSION = "001"
logger = logging.getLogger("writer_witness")


async def database_clock(session: AsyncSession) -> datetime:
    value = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def verify_witness_schema(session: AsyncSession) -> None:
    version = (
        await session.execute(text("SELECT version_num FROM writer_witness_schema_version"))
    ).scalar_one_or_none()
    if version != WITNESS_SCHEMA_VERSION:
        raise WitnessServiceConfigurationError(
            f"writer witness schema mismatch: expected={WITNESS_SCHEMA_VERSION} current={version!r}"
        )


@dataclass(frozen=True)
class WriterWitnessServiceRuntime:
    session_factory: Callable[[], Any]
    private_key_base64: str
    credentials: dict[str, WitnessClientCredential]
    lease_duration_seconds: int = 180
    auth_max_age_seconds: int = 15
    auth_max_future_skew_seconds: int = 5
    clock: Callable[[AsyncSession], Awaitable[datetime]] = database_clock


@dataclass(frozen=True)
class WitnessCommand:
    action: str
    expected_epoch: int
    expected_lease_id: str | None
    request_id: str
    reason: str
    lease_duration_seconds: int


class WitnessServiceConfigurationError(RuntimeError):
    """Raised before serving when witness isolation or secrets are unsafe."""


class WriterWitnessServiceSettings(BaseSettings):
    """Minimal process settings; deliberately excludes all product secrets."""

    logical_authority: str = AUTHORITY_WEBAPP
    physical_site: str | None = None
    writer_witness_service_enabled: bool = False
    writer_witness_database_url: str | None = None
    writer_witness_product_database_user: str | None = None
    writer_witness_require_distinct_database_identity: bool = True
    writer_witness_private_key_file: str | None = None
    writer_witness_public_key: str | None = None
    writer_witness_service_webapp_fi_key_id: str | None = None
    writer_witness_service_webapp_fi_secret: str | None = None
    writer_witness_service_webapp_fi_previous_key_id: str | None = None
    writer_witness_service_webapp_fi_previous_secret: str | None = None
    writer_witness_service_webapp_fi_not_after: str | None = None
    writer_witness_service_webapp_fi_previous_not_after: str | None = None
    writer_witness_service_webapp_ir_key_id: str | None = None
    writer_witness_service_webapp_ir_secret: str | None = None
    writer_witness_service_webapp_ir_previous_key_id: str | None = None
    writer_witness_service_webapp_ir_previous_secret: str | None = None
    writer_witness_service_webapp_ir_not_after: str | None = None
    writer_witness_service_webapp_ir_previous_not_after: str | None = None
    writer_witness_lease_duration_seconds: int = 180
    writer_witness_renew_interval_seconds: int = 30
    writer_witness_safety_margin_seconds: int = 15
    writer_witness_max_clock_skew_seconds: int = 5
    writer_witness_auth_max_age_seconds: int = 15
    writer_witness_authoritative_site: str = SITE_WEBAPP_IR

    class Config:
        extra = "ignore"


def _json_response(payload: dict[str, Any], status_code: int) -> JSONResponse:
    return JSONResponse(
        payload,
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def _state_payload(snapshot) -> dict[str, Any]:
    return {
        "holder_site": snapshot.holder_site,
        "writer_epoch": snapshot.writer_epoch,
        "lease_id": snapshot.lease_id,
        "lease_status": snapshot.lease_status,
        "issued_at": snapshot.issued_at.isoformat() if snapshot.issued_at else None,
        "expires_at": snapshot.expires_at.isoformat() if snapshot.expires_at else None,
        "transition_id": snapshot.transition_id,
    }


def _parse_command(raw_body: bytes, *, default_duration: int) -> WitnessCommand:
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WriterWitnessError("witness command body must be valid JSON") from exc
    required_fields = {
        "contract_version",
        "action",
        "expected_epoch",
        "expected_lease_id",
        "request_id",
        "reason",
    }
    optional_fields = {"lease_duration_seconds"}
    if not isinstance(payload, dict) or not required_fields.issubset(payload):
        raise WriterWitnessError("witness command fields do not match the contract")
    if set(payload) - required_fields - optional_fields:
        raise WriterWitnessError("witness command contains unknown fields")
    if type(payload.get("contract_version")) is not int or payload["contract_version"] != WITNESS_COMMAND_VERSION:
        raise WriterWitnessError("unsupported witness command contract version")
    action = payload.get("action")
    if not isinstance(action, str) or action not in WITNESS_ACTIONS:
        raise WriterWitnessError("unsupported witness command action")
    if type(payload.get("expected_epoch")) is not int or payload["expected_epoch"] < 0:
        raise WriterWitnessError("expected_epoch must be a non-negative integer")
    lease_id = payload.get("expected_lease_id")
    if lease_id is not None and (
        not isinstance(lease_id, str)
        or not lease_id
        or lease_id != lease_id.strip()
        or len(lease_id) > 64
    ):
        raise WriterWitnessError("expected_lease_id is invalid")
    request_id = payload.get("request_id")
    reason = payload.get("reason")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id.strip()) > 64:
        raise WriterWitnessError("request_id is invalid")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
        raise WriterWitnessError("reason is invalid")
    duration = payload.get("lease_duration_seconds", default_duration)
    if type(duration) is not int or duration < 30 or duration > 3600:
        raise WriterWitnessError("lease_duration_seconds is invalid")
    return WitnessCommand(
        action=action,
        expected_epoch=payload["expected_epoch"],
        expected_lease_id=lease_id,
        request_id=request_id.strip(),
        reason=reason.strip(),
        lease_duration_seconds=duration,
    )


def _read_private_key(path_value: str | None, public_key_base64: str | None) -> str:
    path = Path(str(path_value or ""))
    if not path.is_absolute() or not path.is_file():
        raise WitnessServiceConfigurationError(
            "WRITER_WITNESS_PRIVATE_KEY_FILE must be an existing absolute file"
        )
    if path.stat().st_mode & 0o077:
        raise WitnessServiceConfigurationError(
            "writer witness private key must not be group/world accessible"
        )
    private_value = path.read_text(encoding="utf-8").strip()
    try:
        private_raw = base64.b64decode(private_value, validate=True)
        private_key = Ed25519PrivateKey.from_private_bytes(private_raw)
        public_raw = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        configured_public = base64.b64decode(str(public_key_base64 or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise WitnessServiceConfigurationError("writer witness Ed25519 key material is invalid") from exc
    if len(private_raw) != 32 or configured_public != public_raw:
        raise WitnessServiceConfigurationError(
            "writer witness public key does not match the private signing key"
        )
    return private_value


def _service_credentials(
    service_settings: WriterWitnessServiceSettings,
) -> dict[str, WitnessClientCredential]:
    configured = (
        (
            "webapp_fi",
            "current",
            service_settings.writer_witness_service_webapp_fi_key_id,
            service_settings.writer_witness_service_webapp_fi_secret,
            service_settings.writer_witness_service_webapp_fi_not_after,
        ),
        (
            "webapp_ir",
            "current",
            service_settings.writer_witness_service_webapp_ir_key_id,
            service_settings.writer_witness_service_webapp_ir_secret,
            service_settings.writer_witness_service_webapp_ir_not_after,
        ),
        (
            "webapp_fi",
            "previous",
            service_settings.writer_witness_service_webapp_fi_previous_key_id,
            service_settings.writer_witness_service_webapp_fi_previous_secret,
            service_settings.writer_witness_service_webapp_fi_previous_not_after,
        ),
        (
            "webapp_ir",
            "previous",
            service_settings.writer_witness_service_webapp_ir_previous_key_id,
            service_settings.writer_witness_service_webapp_ir_previous_secret,
            service_settings.writer_witness_service_webapp_ir_previous_not_after,
        ),
    )
    result: dict[str, WitnessClientCredential] = {}
    used_secrets: set[str] = set()
    current_campaign_expiry: dict[str, datetime] = {}
    for site, slot, raw_key_id, raw_secret, raw_not_after in configured:
        key_id = str(raw_key_id or "").strip()
        secret = str(raw_secret or "")
        if slot == "previous" and not key_id and not secret:
            if raw_not_after:
                raise WitnessServiceConfigurationError(
                    f"orphan witness credential expiry exists for {site}:{slot}"
                )
            continue
        if not key_id or len(key_id) > 64 or len(secret.encode("utf-8")) < 32:
            raise WitnessServiceConfigurationError(
                f"dedicated witness HMAC credential is missing or unsafe for {site}:{slot}"
            )
        if key_id in result:
            raise WitnessServiceConfigurationError("writer witness key ids must be unique")
        if secret in used_secrets:
            raise WitnessServiceConfigurationError("writer witness HMAC secrets must be unique")
        not_after: datetime | None = None
        not_after_text = str(raw_not_after or "").strip()
        expected_short_site = site.removeprefix("webapp_")
        campaign_key = re.fullmatch(
            rf"matrix-wwm_[0-9a-f]{{12}}-{expected_short_site}",
            key_id,
        ) is not None
        if campaign_key and slot != "current":
            raise WitnessServiceConfigurationError(
                f"a previous witness credential cannot be a Matrix key for {site}"
            )
        if not_after_text:
            if not_after_text.endswith("Z"):
                not_after_text = not_after_text[:-1] + "+00:00"
            try:
                not_after = datetime.fromisoformat(not_after_text)
            except ValueError as exc:
                raise WitnessServiceConfigurationError(
                    f"writer witness campaign expiry is invalid for {site}:{slot}"
                ) from exc
            if not_after.tzinfo is None:
                raise WitnessServiceConfigurationError(
                    f"writer witness campaign expiry lacks timezone for {site}:{slot}"
                )
            not_after = not_after.astimezone(timezone.utc)
        if campaign_key:
            if not_after is None:
                raise WitnessServiceConfigurationError(
                    f"campaign expiry does not match the witness credential for {site}:{slot}"
                )
            current_campaign_expiry[site] = not_after
        elif not_after is not None:
            # During a bounded Matrix campaign the pre-campaign credential is
            # retained in the previous slot and intentionally receives the
            # exact same expiry.  No other non-campaign credential may carry a
            # campaign expiry.
            if (
                slot != "previous"
                or current_campaign_expiry.get(site) != not_after
            ):
                raise WitnessServiceConfigurationError(
                    f"campaign expiry does not match the witness credential for {site}:{slot}"
                )
        result[key_id] = WitnessClientCredential(
            key_id=key_id,
            site=site,
            secret=secret,
            not_after=not_after,
        )
        used_secrets.add(secret)
    return result


def _build_runtime_from_settings(
    service_settings: WriterWitnessServiceSettings | None = None,
) -> tuple[WriterWitnessServiceRuntime, Any]:
    configured = service_settings or WriterWitnessServiceSettings()
    if not configured.writer_witness_service_enabled:
        raise WitnessServiceConfigurationError("WRITER_WITNESS_SERVICE_ENABLED must be true")
    configured_site = str(configured.physical_site or "").strip().lower()
    if (
        configured_site != SITE_WEBAPP_IR
        or str(configured.logical_authority).strip().lower() != AUTHORITY_WEBAPP
        or configured.writer_witness_authoritative_site != SITE_WEBAPP_IR
    ):
        raise WitnessServiceConfigurationError(
            "the witness service requires explicit PHYSICAL_SITE=webapp_ir"
        )
    database_url = str(configured.writer_witness_database_url or "").strip()
    if not database_url:
        raise WitnessServiceConfigurationError("WRITER_WITNESS_DATABASE_URL is mandatory")
    parsed_database_url = make_url(database_url)
    if parsed_database_url.drivername != "postgresql+asyncpg" or not parsed_database_url.database:
        raise WitnessServiceConfigurationError(
            "writer witness database must use an explicit postgresql+asyncpg database URL"
        )
    if configured.writer_witness_require_distinct_database_identity:
        witness_user = parsed_database_url.username
        product_user = str(configured.writer_witness_product_database_user or "").strip()
        if not witness_user or not product_user or witness_user == product_user:
            raise WitnessServiceConfigurationError(
                "writer witness database must use a distinct least-privilege identity"
            )
    if not witness_timing_configuration_is_safe(
        lease_duration_seconds=configured.writer_witness_lease_duration_seconds,
        renew_interval_seconds=configured.writer_witness_renew_interval_seconds,
        safety_margin_seconds=configured.writer_witness_safety_margin_seconds,
        max_clock_skew_seconds=configured.writer_witness_max_clock_skew_seconds,
    ):
        raise WitnessServiceConfigurationError("writer witness timing configuration is unsafe")
    if (
        configured.writer_witness_auth_max_age_seconds
        <= configured.writer_witness_max_clock_skew_seconds
        or configured.writer_witness_auth_max_age_seconds > 60
    ):
        raise WitnessServiceConfigurationError("writer witness authentication window is unsafe")
    private_key = _read_private_key(
        configured.writer_witness_private_key_file,
        configured.writer_witness_public_key,
    )
    credentials = _service_credentials(configured)
    engine = create_async_engine(
        database_url,
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=300,
    )
    sessions = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    runtime = WriterWitnessServiceRuntime(
        session_factory=sessions,
        private_key_base64=private_key,
        credentials=credentials,
        lease_duration_seconds=configured.writer_witness_lease_duration_seconds,
        auth_max_age_seconds=configured.writer_witness_auth_max_age_seconds,
        auth_max_future_skew_seconds=configured.writer_witness_max_clock_skew_seconds,
    )
    return runtime, engine


def create_writer_witness_app(
    runtime: WriterWitnessServiceRuntime | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = None
        try:
            if runtime is None:
                active_runtime, engine = _build_runtime_from_settings()
                app.state.writer_witness_runtime = active_runtime
                async with active_runtime.session_factory() as session:
                    await verify_witness_schema(session)
                    await active_runtime.clock(session)
                    await load_witness_snapshot(session)
            else:
                app.state.writer_witness_runtime = runtime
            yield
        finally:
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title="WebApp Writer Witness",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    if runtime is not None:
        # ASGI test transports may not run lifespan automatically. Keeping the
        # injected, non-production runtime available is deterministic and does
        # not weaken the settings-built service startup checks.
        app.state.writer_witness_runtime = runtime

    def active_runtime(request: Request) -> WriterWitnessServiceRuntime:
        value = getattr(request.app.state, "writer_witness_runtime", None)
        if value is None:
            raise WitnessServiceConfigurationError("writer witness runtime is not initialized")
        return value

    async def authenticate(
        request: Request,
        service_runtime: WriterWitnessServiceRuntime,
        session: AsyncSession,
        raw_body: bytes,
    ):
        witness_now = await service_runtime.clock(session)
        caller = verify_witness_request(
            credentials=service_runtime.credentials,
            method=request.method,
            path=request.url.path,
            body=raw_body,
            headers=dict(request.headers),
            now=witness_now,
            max_age_seconds=service_runtime.auth_max_age_seconds,
            max_future_skew_seconds=service_runtime.auth_max_future_skew_seconds,
        )
        return caller, witness_now

    @app.get("/health/live")
    async def health_live():
        return {"status": "alive", "service": "writer-witness"}

    @app.get("/health/ready")
    async def health_ready(request: Request):
        service_runtime = active_runtime(request)
        try:
            async with service_runtime.session_factory() as session:
                await verify_witness_schema(session)
                await service_runtime.clock(session)
                await load_witness_snapshot(session)
        except Exception:
            return _json_response({"status": "not_ready"}, 503)
        return _json_response({"status": "ready"}, 200)

    @app.get(STATUS_PATH)
    async def witness_status(request: Request):
        service_runtime = active_runtime(request)
        raw_body = await request.body()
        try:
            async with service_runtime.session_factory() as session:
                caller, witness_now = await authenticate(
                    request, service_runtime, session, raw_body
                )
                snapshot = await load_witness_snapshot(session)
        except WitnessAuthenticationError as exc:
            return _json_response({"accepted": False, "code": exc.code}, 401)
        return _json_response(
            {
                "contract_version": WITNESS_COMMAND_VERSION,
                "accepted": True,
                "request_id": caller.request_id,
                "witness_time": witness_now.isoformat(),
                "state": _state_payload(snapshot),
            },
            200,
        )

    @app.post(TRANSITION_PATH)
    async def witness_transition(request: Request):
        service_runtime = active_runtime(request)
        raw_body = await request.body()
        try:
            async with service_runtime.session_factory() as session:
                caller, witness_now = await authenticate(
                    request, service_runtime, session, raw_body
                )
                command = _parse_command(
                    raw_body,
                    default_duration=service_runtime.lease_duration_seconds,
                )
                if command.request_id != caller.request_id:
                    raise WitnessAuthenticationError(
                        "signed request id does not match the command body"
                    )
                # Credential generations rotate, but the durable business
                # request belongs to the authenticated physical site.  Keep
                # key identity in logs without making exact overlap retries
                # collide with their own persisted receipt.
                operator = f"hmac:{caller.site}"
                try:
                    result = await transition_witness_state(
                        session,
                        action=command.action,
                        requester_site=caller.site,
                        expected_epoch=command.expected_epoch,
                        expected_lease_id=command.expected_lease_id,
                        request_id=command.request_id,
                        operator=operator,
                        reason=command.reason,
                        private_key_base64=(
                            service_runtime.private_key_base64
                            if command.action in {"acquire", "renew"}
                            else None
                        ),
                        lease_duration_seconds=command.lease_duration_seconds,
                        authorization_not_after=caller.credential_not_after,
                        clock=service_runtime.clock,
                    )
                except WriterWitnessCampaignExpiredError as exc:
                    await session.rollback()
                    return _json_response(
                        {"accepted": False, "code": exc.code},
                        401,
                    )
                except WriterWitnessError as exc:
                    rejection = await persist_witness_rejection(
                        session,
                        action=command.action,
                        requester_site=caller.site,
                        expected_epoch=command.expected_epoch,
                        expected_lease_id=command.expected_lease_id,
                        request_id=command.request_id,
                        operator=operator,
                        reason=command.reason,
                        lease_duration_seconds=command.lease_duration_seconds,
                        error=exc,
                    )
                    await session.commit()
                    logger.warning(
                        "Writer witness transition rejected",
                        extra={
                            "event": "writer_witness.transition.rejected",
                            "request_id": command.request_id,
                            "requester_site": caller.site,
                            "credential_key_id": caller.key_id,
                            "action": command.action,
                            "error_code": rejection.code,
                            "replayed": rejection.replayed,
                        },
                    )
                    return _json_response(
                        {
                            "contract_version": WITNESS_COMMAND_VERSION,
                            "accepted": False,
                            "code": rejection.code,
                            "detail": str(rejection),
                            "request_id": command.request_id,
                            "replayed": rejection.replayed,
                        },
                        409,
                    )
                await session.commit()
        except WitnessAuthenticationError as exc:
            return _json_response({"accepted": False, "code": exc.code}, 401)
        except WriterWitnessError as exc:
            return _json_response(
                {"accepted": False, "code": exc.code, "detail": str(exc)},
                422,
            )
        except Exception:
            logger.exception(
                "Writer witness transition failed closed",
                extra={"event": "writer_witness.transition.error"},
            )
            return _json_response(
                {"accepted": False, "code": "witness_internal_error"},
                503,
            )
        payload = result.as_payload()
        payload.update(request_id=command.request_id, replayed=result.replayed)
        logger.info(
            "Writer witness transition accepted",
            extra={
                "event": "writer_witness.transition.accepted",
                "request_id": command.request_id,
                "requester_site": caller.site,
                "credential_key_id": caller.key_id,
                "action": command.action,
                "writer_epoch": result.state.writer_epoch,
                "lease_id": result.state.lease_id,
                "replayed": result.replayed,
            },
        )
        return _json_response(payload, 200)

    return app


app = create_writer_witness_app()
