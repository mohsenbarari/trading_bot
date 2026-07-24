"""Private, separately deployable control API for the WebApp writer witness."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

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

from core.human_approval import (
    HumanApprovalError,
    issue_human_approval_relay_receipt,
    parse_human_approval_relay_command,
)
from core.runtime_sites import AUTHORITY_WEBAPP, SITE_WEBAPP_IR, WEBAPP_SITES
from core.secure_file_io import SecureFileError, read_secure_text
from core.writer_witness_auth import (
    WITNESS_HUMAN_APPROVAL_RELAY_PATH,
    WITNESS_STATUS_PATH,
    WITNESS_OPERATION_PATH,
    WITNESS_RELAY_ORCHESTRATOR_SITE,
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
WITNESS_SCHEMA_VERSION = "003"
logger = logging.getLogger("writer_witness")


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


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
    database_user: str | None = None
    human_approval_relay_enabled: bool = False
    human_approval_relay_session_file: str | None = None
    human_approval_relay_policy_file: str | None = None


async def verify_witness_runtime_database_role(
    session: AsyncSession,
    *,
    expected_user: str,
) -> None:
    """Reject owner, migrator, DDL-capable, or under-granted runtime identities."""

    row = (
        await session.execute(
            text(
                "SELECT current_user AS database_user, "
                "pg_get_userbyid(database_definition.datdba) AS database_owner, "
                "has_database_privilege(current_user, current_database(), 'CREATE') AS database_create, "
                "has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create, "
                "has_table_privilege(current_user, 'writer_witness_schema_version', 'SELECT') AS schema_read, "
                "(has_table_privilege(current_user, 'webapp_writer_witness_state', 'SELECT') "
                " AND has_table_privilege(current_user, 'webapp_writer_witness_state', 'UPDATE')) AS state_dml, "
                "(has_table_privilege(current_user, 'webapp_writer_witness_receipts', 'SELECT') "
                " AND has_table_privilege(current_user, 'webapp_writer_witness_receipts', 'INSERT')) AS receipt_dml, "
                "(has_table_privilege(current_user, 'dr_failover_operation_ledger', 'SELECT') "
                " AND has_table_privilege(current_user, 'dr_failover_operation_ledger', 'INSERT') "
                " AND has_table_privilege(current_user, 'dr_failover_operation_ledger', 'UPDATE')) AS ledger_dml, "
                "(has_table_privilege(current_user, 'human_approval_relay_receipts', 'SELECT') "
                " AND has_table_privilege(current_user, 'human_approval_relay_receipts', 'INSERT')) AS relay_dml, "
                "(SELECT count(*) FROM pg_class object "
                " JOIN pg_namespace namespace ON namespace.oid=object.relnamespace "
                " JOIN pg_roles owner ON owner.oid=object.relowner "
                " WHERE namespace.nspname='public' AND owner.rolname=current_user) AS owned_objects "
                "FROM pg_database database_definition "
                "WHERE database_definition.datname=current_database()"
            )
        )
    ).mappings().one_or_none()
    if row is None:
        raise WitnessServiceConfigurationError("writer witness database role evidence is missing")
    if (
        row["database_user"] != expected_user
        or row["database_owner"] == expected_user
        or row["database_create"] is True
        or row["schema_create"] is True
        or int(row["owned_objects"] or 0) != 0
        or not all(
            row[name]
            for name in (
                "schema_read", "state_dml", "receipt_dml", "ledger_dml", "relay_dml"
            )
        )
    ):
        raise WitnessServiceConfigurationError(
            "writer witness runtime database identity is not least privilege"
        )


@dataclass(frozen=True)
class WitnessCommand:
    action: str
    expected_epoch: int
    expected_lease_id: str | None
    request_id: str
    reason: str
    lease_duration_seconds: int


@dataclass(frozen=True)
class FailoverOperationCommand:
    action: str
    operation_id: str
    operation_nonce: str
    plan_hash: str
    expires_at: datetime
    outcome: str | None
    evidence_hash: str | None


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
    human_approval_relay_enabled: bool = False
    human_approval_relay_session_file: str | None = None
    human_approval_relay_policy_file: str | None = None
    human_approval_relay_orchestrator_key_id: str | None = None
    human_approval_relay_orchestrator_secret: str | None = None
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


def _parse_failover_operation(raw_body: bytes) -> FailoverOperationCommand:
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WriterWitnessError("failover operation body must be valid JSON") from exc
    common = {
        "contract_version", "action", "operation_id", "operation_nonce",
        "plan_hash", "expires_at",
    }
    action = payload.get("action") if isinstance(payload, dict) else None
    required = common if action == "reserve" else common | {"outcome", "evidence_hash"}
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or payload.get("contract_version") != 1
        or action not in {"reserve", "finalize"}
    ):
        raise WriterWitnessError("failover operation fields do not match the contract")
    try:
        operation_id = str(UUID(str(payload["operation_id"])))
        operation_nonce = str(UUID(str(payload["operation_nonce"])))
        expires_at = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise WriterWitnessError("failover operation identity/time is invalid") from exc
    if expires_at.tzinfo is None:
        raise WriterWitnessError("failover operation expiry lacks timezone")
    plan_hash = str(payload["plan_hash"])
    outcome = payload.get("outcome")
    evidence_hash = payload.get("evidence_hash")
    if (
        operation_id == operation_nonce
        or not re.fullmatch(r"[0-9a-f]{64}", plan_hash)
        or (action == "finalize" and outcome not in {"completed", "rolled_back"})
        or (
            action == "finalize"
            and not re.fullmatch(r"[0-9a-f]{64}", str(evidence_hash))
        )
    ):
        raise WriterWitnessError("failover operation values are invalid")
    return FailoverOperationCommand(
        action=action,
        operation_id=operation_id,
        operation_nonce=operation_nonce,
        plan_hash=plan_hash,
        expires_at=expires_at.astimezone(timezone.utc),
        outcome=str(outcome) if outcome is not None else None,
        evidence_hash=str(evidence_hash) if evidence_hash is not None else None,
    )


def _signed_operation_receipt(
    runtime: WriterWitnessServiceRuntime,
    *,
    command: FailoverOperationCommand,
    status: str,
    receipt_id: str,
    receipt_hash: str,
) -> dict[str, Any]:
    unsigned = {
        "contract_version": 1,
        "status": status,
        "operation_id": command.operation_id,
        "operation_nonce": command.operation_nonce,
        "plan_hash": command.plan_hash,
        "ledger_receipt_hash": receipt_hash,
        "ledger_receipt_id": receipt_id,
    }
    private = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(runtime.private_key_base64, validate=True)
    )
    return {
        **unsigned,
        "witness_signature": base64.b64encode(
            private.sign(_canonical_json_bytes(unsigned))
        ).decode("ascii"),
    }


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


def _relay_document_path(path_value: str | None, *, label: str) -> str:
    """Require an owner-only, regular JSON document mounted into the service."""

    path = Path(str(path_value or ""))
    if not path.is_absolute():
        raise WitnessServiceConfigurationError(f"{label} must be an absolute path")
    try:
        value = json.loads(read_secure_text(path, label=label, max_size=1024 * 1024))
    except (SecureFileError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise WitnessServiceConfigurationError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise WitnessServiceConfigurationError(f"{label} must be a JSON object")
    return str(path)


def _load_relay_document(path_value: str | None, *, label: str) -> dict[str, Any]:
    path = Path(str(path_value or ""))
    try:
        value = json.loads(read_secure_text(path, label=label, max_size=1024 * 1024))
    except (SecureFileError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise WitnessServiceConfigurationError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise WitnessServiceConfigurationError(f"{label} must be a JSON object")
    return value


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
    if service_settings.human_approval_relay_enabled:
        key_id = str(service_settings.human_approval_relay_orchestrator_key_id or "").strip()
        secret = str(service_settings.human_approval_relay_orchestrator_secret or "")
        if (
            not key_id
            or len(key_id) > 64
            or len(secret.encode("utf-8")) < 32
            or key_id in result
            or secret in used_secrets
        ):
            raise WitnessServiceConfigurationError(
                "dedicated human approval relay credential is missing or unsafe"
            )
        result[key_id] = WitnessClientCredential(
            key_id=key_id,
            site=WITNESS_RELAY_ORCHESTRATOR_SITE,
            secret=secret,
        )
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
    relay_session_file: str | None = None
    relay_policy_file: str | None = None
    if configured.human_approval_relay_enabled:
        relay_session_file = _relay_document_path(
            configured.human_approval_relay_session_file,
            label="human approval relay session",
        )
        relay_policy_file = _relay_document_path(
            configured.human_approval_relay_policy_file,
            label="human approval relay policy",
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
        database_user=str(parsed_database_url.username),
        human_approval_relay_enabled=configured.human_approval_relay_enabled,
        human_approval_relay_session_file=relay_session_file,
        human_approval_relay_policy_file=relay_policy_file,
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
                    if active_runtime.database_user:
                        await verify_witness_runtime_database_role(
                            session,
                            expected_user=active_runtime.database_user,
                        )
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
        *,
        allowed_sites: frozenset[str] = WEBAPP_SITES,
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
        if caller.site not in allowed_sites:
            raise WitnessAuthenticationError("witness caller is not authorized for this endpoint")
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
                if service_runtime.database_user:
                    await verify_witness_runtime_database_role(
                        session,
                        expected_user=service_runtime.database_user,
                    )
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

    @app.post(WITNESS_OPERATION_PATH)
    async def failover_operation_ledger(request: Request):
        """Reserve/finalize one saga exactly once in the independent Witness DB."""

        service_runtime = active_runtime(request)
        raw_body = await request.body()
        try:
            async with service_runtime.session_factory() as session:
                caller, witness_now = await authenticate(
                    request, service_runtime, session, raw_body
                )
                command = _parse_failover_operation(raw_body)
                expected_request_id = (
                    command.operation_nonce
                    if command.action == "reserve"
                    else command.operation_id
                )
                if caller.request_id != expected_request_id:
                    raise WitnessAuthenticationError(
                        "signed request id does not match the failover operation"
                    )
                row = (
                    await session.execute(
                        text(
                            "SELECT operation_id, operation_nonce, plan_hash, status, "
                            "reservation_receipt_id, reservation_receipt_hash, "
                            "final_evidence_hash, expires_at FROM dr_failover_operation_ledger "
                            "WHERE operation_id = :operation_id "
                            "OR operation_nonce = :operation_nonce FOR UPDATE"
                        ),
                        {
                            "operation_id": command.operation_id,
                            "operation_nonce": command.operation_nonce,
                        },
                    )
                ).mappings().one_or_none()
                if command.action == "reserve":
                    if row is None:
                        if witness_now >= command.expires_at:
                            raise WriterWitnessError("expired failover plan cannot be reserved")
                        receipt_id = str(uuid4())
                        receipt_payload = {
                            "operation_id": command.operation_id,
                            "operation_nonce": command.operation_nonce,
                            "plan_hash": command.plan_hash,
                            "expires_at": command.expires_at.isoformat(),
                            "receipt_id": receipt_id,
                        }
                        receipt_hash = hashlib.sha256(
                            _canonical_json_bytes(receipt_payload)
                        ).hexdigest()
                        await session.execute(
                            text(
                                "INSERT INTO dr_failover_operation_ledger "
                                "(operation_id, operation_nonce, plan_hash, status, expires_at, "
                                "reservation_receipt_id, reservation_receipt_hash) VALUES "
                                "(:operation_id, :operation_nonce, :plan_hash, 'reserved', "
                                ":expires_at, :receipt_id, :receipt_hash)"
                            ),
                            {
                                "operation_id": command.operation_id,
                                "operation_nonce": command.operation_nonce,
                                "plan_hash": command.plan_hash,
                                "expires_at": command.expires_at,
                                "receipt_id": receipt_id,
                                "receipt_hash": receipt_hash,
                            },
                        )
                        status = "reserved"
                    else:
                        if (
                            row["operation_id"] != command.operation_id
                            or
                            row["operation_nonce"] != command.operation_nonce
                            or row["plan_hash"] != command.plan_hash
                        ):
                            raise WriterWitnessError(
                                "failover operation id was consumed by another plan"
                            )
                        receipt_id = str(row["reservation_receipt_id"])
                        receipt_hash = str(row["reservation_receipt_hash"])
                        status = (
                            "expired"
                            if row["status"] == "reserved"
                            and witness_now >= row["expires_at"]
                            else "existing"
                        )
                else:
                    if row is None or (
                        row["operation_id"] != command.operation_id
                        or row["operation_nonce"] != command.operation_nonce
                        or row["plan_hash"] != command.plan_hash
                    ):
                        raise WriterWitnessError(
                            "failover operation was not independently reserved"
                        )
                    if row["status"] == "reserved":
                        await session.execute(
                            text(
                                "UPDATE dr_failover_operation_ledger SET status = :status, "
                                "final_evidence_hash = :evidence_hash, "
                                "finalized_at = clock_timestamp(), updated_at = clock_timestamp() "
                                "WHERE operation_id = :operation_id"
                            ),
                            {
                                "status": command.outcome,
                                "evidence_hash": command.evidence_hash,
                                "operation_id": command.operation_id,
                            },
                        )
                    elif (
                        row["status"] != command.outcome
                        or row["final_evidence_hash"] != command.evidence_hash
                    ):
                        raise WriterWitnessError(
                            "failover operation already has another final outcome"
                        )
                    receipt_id = str(row["reservation_receipt_id"])
                    receipt_hash = hashlib.sha256(
                        _canonical_json_bytes(
                            {
                                "reservation_receipt_hash": row["reservation_receipt_hash"],
                                "outcome": command.outcome,
                                "evidence_hash": command.evidence_hash,
                            }
                        )
                    ).hexdigest()
                    status = str(command.outcome)
                await session.commit()
        except WitnessAuthenticationError as exc:
            return _json_response({"accepted": False, "code": exc.code}, 401)
        except WriterWitnessError as exc:
            return _json_response(
                {"accepted": False, "code": exc.code, "detail": str(exc)}, 409
            )
        except Exception:
            logger.exception(
                "Witness failover operation ledger failed closed",
                extra={"event": "writer_witness.failover_operation.error"},
            )
            return _json_response(
                {"accepted": False, "code": "witness_operation_ledger_error"}, 503
            )
        return _json_response(
            _signed_operation_receipt(
                service_runtime,
                command=command,
                status=status,
                receipt_id=receipt_id,
                receipt_hash=receipt_hash,
            ),
            200,
        )

    @app.post(WITNESS_HUMAN_APPROVAL_RELAY_PATH)
    async def human_approval_relay(request: Request):
        """Issue one exact, Witness-signed receipt from the local 48-hour session.

        The issuer-signed session remains mounted read-only on the Witness.
        A controller proves only its dedicated pairwise HMAC identity and gets
        back a receipt bound to the signed request id, action and subject.
        """

        service_runtime = active_runtime(request)
        raw_body = await request.body()
        if not service_runtime.human_approval_relay_enabled:
            return _json_response(
                {"accepted": False, "code": "human_approval_relay_disabled"}, 404
            )
        try:
            async with service_runtime.session_factory() as session:
                caller, witness_now = await authenticate(
                    request,
                    service_runtime,
                    session,
                    raw_body,
                    allowed_sites=frozenset({WITNESS_RELAY_ORCHESTRATOR_SITE}),
                )
                command = parse_human_approval_relay_command(json.loads(raw_body))
                if command.request_id != caller.request_id:
                    raise WitnessAuthenticationError(
                        "signed request id does not match the approval relay command"
                    )
                request_hash = hashlib.sha256(raw_body).hexdigest()
                row = (
                    await session.execute(
                        text(
                            "SELECT request_sha256, receipt FROM human_approval_relay_receipts "
                            "WHERE request_id = :request_id FOR UPDATE"
                        ),
                        {"request_id": command.request_id},
                    )
                ).mappings().one_or_none()
                if row is not None:
                    if row["request_sha256"] != request_hash:
                        raise HumanApprovalError(
                            "human approval relay request id was consumed by another command"
                        )
                    receipt = row["receipt"]
                    if not isinstance(receipt, dict):
                        raise WitnessServiceConfigurationError(
                            "stored human approval relay receipt is invalid"
                        )
                else:
                    session_token = _load_relay_document(
                        service_runtime.human_approval_relay_session_file,
                        label="human approval relay session",
                    )
                    policy = _load_relay_document(
                        service_runtime.human_approval_relay_policy_file,
                        label="human approval relay policy",
                    )
                    private = Ed25519PrivateKey.from_private_bytes(
                        base64.b64decode(service_runtime.private_key_base64, validate=True)
                    )
                    receipt = issue_human_approval_relay_receipt(
                        session_token,
                        policy_payload=policy,
                        command=command,
                        witness_private_key=private,
                        now=witness_now,
                        receipt_id=str(uuid4()),
                    )
                    await session.execute(
                        text(
                            "INSERT INTO human_approval_relay_receipts "
                            "(request_id, request_sha256, approval_id, action, subject_sha256, "
                            "session_token_sha256, expires_at, receipt) VALUES "
                            "(:request_id, :request_sha256, :approval_id, :action, "
                            ":subject_sha256, :session_token_sha256, :expires_at, "
                            "CAST(:receipt AS jsonb))"
                        ),
                        {
                            "request_id": command.request_id,
                            "request_sha256": request_hash,
                            "approval_id": receipt["approval_id"],
                            "action": command.action,
                            "subject_sha256": hashlib.sha256(
                                _canonical_json_bytes(command.subject)
                            ).hexdigest(),
                            "session_token_sha256": receipt["session_token_sha256"],
                            "expires_at": receipt["expires_at"],
                            "receipt": json.dumps(receipt, sort_keys=True, separators=(",", ":")),
                        },
                    )
                    await session.commit()
        except WitnessAuthenticationError as exc:
            return _json_response({"accepted": False, "code": exc.code}, 401)
        except (HumanApprovalError, json.JSONDecodeError):
            return _json_response(
                {"accepted": False, "code": "human_approval_relay_rejected"}, 409
            )
        except Exception:
            logger.exception(
                "Witness human approval relay failed closed",
                extra={"event": "writer_witness.human_approval_relay.error"},
            )
            return _json_response(
                {"accepted": False, "code": "human_approval_relay_unavailable"}, 503
            )
        return _json_response(receipt, 200)

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
