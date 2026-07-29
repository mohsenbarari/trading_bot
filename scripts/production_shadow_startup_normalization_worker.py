#!/usr/bin/env python3
"""Normalize startup-only database state on one prepared production clone.

Only the exact operation-owned PostgreSQL container and its internal network
may already exist.  The worker runs the release's real database-only startup
mutation path twice in bounded, provider-free one-off containers, proves the
second invocation is a logical no-op, and leaves every operation container
stopped.  It never starts Redis, a public service, or a background worker.

The CLI is a bounded stdio protocol.  Mutation additionally requires a
controller authority acknowledgement before every forward step.  Merely
invoking the module without ``--apply`` is plan-only.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import sys
import threading
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import UUID


sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import production_shadow_global_docker_inventory_agent as INVENTORY  # noqa: E402
from scripts import production_shadow_precommit_worker as PRECOMMIT  # noqa: E402
from scripts import wa_ir_production_operation as WA_OPERATION  # noqa: E402


REQUEST_SCHEMA = "production-shadow-startup-normalization-request-v1"
RESULT_SCHEMA = "production-shadow-startup-normalization-result-v1"
PLAN_SCHEMA = "production-shadow-startup-normalization-plan-v1"
AUTHORITY_REQUEST_SCHEMA = (
    "production-shadow-startup-normalization-authority-request-v1"
)
AUTHORITY_RESPONSE_SCHEMA = (
    "production-shadow-startup-normalization-authority-response-v1"
)
ERROR_SCHEMA = "production-shadow-startup-normalization-error-v1"

ROLE_ORDER = ("bot_fi", "webapp_fi", "webapp_ir")
ROLE_HOSTS = dict(INVENTORY.ROLE_HOSTS)
WORKER_RELATIVE = Path(
    "scripts/production_shadow_startup_normalization_worker.py"
)
INVENTORY_RELATIVE = Path(
    "scripts/production_shadow_global_docker_inventory_agent.py"
)
CONTRACT_WORKER_RELATIVES = {
    "bot_fi": Path("scripts/production_shadow_precommit_worker.py"),
    "webapp_fi": Path("scripts/production_shadow_precommit_worker.py"),
    "webapp_ir": Path("scripts/wa_ir_production_operation.py"),
}

SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
NETWORK_ID_RE = re.compile(r"^[0-9a-f]{64}$")
ZERO_SHA256 = "0" * 64
MAX_CONTROL_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_RELEASE_FILE_BYTES = 16 * 1024 * 1024
MIN_LIFETIME_SECONDS = 15.0
MAX_LIFETIME_SECONDS = 7200.0
FUTURE_SKEW_SECONDS = 5.0

FORBIDDEN_PROVIDER_ENV = frozenset(
    set(WA_OPERATION._FORBIDDEN_PREPARE_ENV_NAMES)  # noqa: SLF001
    | {
        "BOT_TOKEN",
        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
        "TELEGRAM_WEBAPP_VALIDATION_KEY",
        "SMSIR_API_KEY",
        "SMSIR_LINE_NUMBER",
        "SMSIR_OTP_TEMPLATE_ID",
        "SMSIR_OTP_TEMPLATE_PARAMETER",
        "SMSIR_INVITATION_TEMPLATE_ID",
        "SMSIR_INVITATION_TEMPLATE_PARAMETER",
        "SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID",
        "SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID",
        "WEB_PUSH_ENABLED",
        "WEB_PUSH_VAPID_PUBLIC_KEY",
        "WEB_PUSH_VAPID_PRIVATE_KEY",
        "WEB_PUSH_VAPID_SUBJECT",
        "DR_SYNC_PEER_URLS_JSON",
        "DR_SYNC_PAIRWISE_KEYS_JSON",
        "DR_BLOB_S3_CREDENTIALS_FILE",
        "DR_BLOB_ENCRYPTION_KEYRING_FILE",
    }
)
WA_ROLE_NORMALIZATION_ENV_FIELDS = frozenset(
    {
        "TZ",
        "ENVIRONMENT",
        "TRUSTED_PROXY_CIDRS",
        "TOPOLOGY_SCHEMA_VERSION",
        "THREE_SITE_DR_ENABLED",
        "DR_EVENT_PROTOCOL_ENABLED",
        "DR_EVENT_PROTOCOL_STRICT",
        "DR_SYNC_VERIFY_TLS",
        "DR_SYNC_CA_BUNDLE",
        "RELEASE_SHA",
        "BACKGROUND_JOBS_ENABLED",
        "SERVER_MODE",
        "LOGICAL_AUTHORITY",
        "PHYSICAL_SITE",
        "DATABASE_URL",
        "SYNC_DATABASE_URL",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "FRONTEND_URL",
        "PUBLIC_WEBAPP_URL",
        "JWT_SECRET_KEY",
        "REDIS_URL",
        "REDIS_HOST",
        "THREE_SITE_APP_DB_PASSWORD",
        "THREE_SITE_RECEIVER_DB_PASSWORD",
        "THREE_SITE_DELIVERY_DB_PASSWORD",
        "THREE_SITE_PROJECTION_DB_PASSWORD",
        "THREE_SITE_BLOB_DB_PASSWORD",
        "THREE_SITE_EFFECT_DB_PASSWORD",
        "THREE_SITE_CONTROL_DB_PASSWORD",
        "THREE_SITE_OBSERVER_DB_PASSWORD",
    }
)
EXPECTED_CONSTRAINTS = {
    "operation_owned_clone_only": True,
    "prepared_database_running_healthy_required": True,
    "exact_bound_stopped_database_restart_allowed": True,
    "database_start_reconciliation_required": True,
    "internal_network_only": True,
    "redis_start_forbidden": True,
    "public_service_start_forbidden": True,
    "background_jobs_forbidden": True,
    "provider_credentials_forbidden": True,
    "legacy_mutation_forbidden": True,
    "current_mutation_forbidden": True,
    "volume_mutation_forbidden": True,
    "object_storage_forbidden": True,
    "two_independent_invocations_required": True,
    "second_invocation_zero_delta_required": True,
    "all_operation_containers_stopped_on_exit": True,
}

REQUEST_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "expected_host",
        "controller_challenge_sha256",
        "issued_at",
        "expires_at",
        "worker_path",
        "worker_sha256",
        "inventory_agent_sha256",
        "contract_worker_sha256",
        "role_manifest_path",
        "role_manifest_sha256",
        "pre_inventory_request",
        "pre_inventory_response",
        "constraints",
        "request_binding_sha256",
    }
)

STATE_FIELDS = frozenset(
    {
        "database_fingerprint_sha256",
        "database_row_count",
        "database_table_count",
        "uploads_tree_sha256",
        "audit_tree_sha256",
        "redis_tree_sha256",
        "data_state_sha256",
    }
)
RESULT_FIELDS = frozenset(
    {
        "schema",
        "status",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "expected_host",
        "controller_challenge_sha256",
        "issued_at",
        "expires_at",
        "captured_at",
        "completed_at",
        "request_binding_sha256",
        "pre_inventory_response_sha256",
        "prepared_container_id",
        "prepared_network_id",
        "prepared_container_identity_sha256",
        "prepared_container_metadata_sha256",
        "prepared_network_identity_sha256",
        "prepared_network_metadata_sha256",
        "prepared_config_sha256",
        "prepared_environment_sha256",
        "prepared_environment_entry_count",
        "prepared_compose_config_sha256",
        "prepared_host_config_sha256",
        "prepared_mounts_sha256",
        "prepared_network_attachment_sha256",
        "before_state",
        "first_invocation_state",
        "second_invocation_state",
        "normalization_command_sha256",
        "normalization_invocation_count",
        "second_start_database_delta_count",
        "second_start_data_delta_count",
        "provider_credentials_present",
        "provider_network_used",
        "background_jobs_enabled",
        "redis_started",
        "public_service_started",
        "oneoff_residue_count",
        "database_stop_performed",
        "database_was_running_at_reconciliation",
        "database_start_performed",
        "operation_owned_running_container_count",
        "persistent_resource_removed",
        "legacy_mutated",
        "current_mutated",
        "volume_mutated",
        "object_storage_used",
        "response_sha256",
    }
)

NORMALIZATION_PROGRAM = r"""
import asyncio
import json
import os

for _name in (
    "BOT_TOKEN",
    "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
    "TELEGRAM_WEBAPP_VALIDATION_KEY",
    "SMSIR_API_KEY",
    "SMSIR_LINE_NUMBER",
    "SMSIR_OTP_TEMPLATE_ID",
    "SMSIR_OTP_TEMPLATE_PARAMETER",
    "SMSIR_INVITATION_TEMPLATE_ID",
    "SMSIR_INVITATION_TEMPLATE_PARAMETER",
    "SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID",
    "SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID",
    "WEB_PUSH_ENABLED",
    "WEB_PUSH_VAPID_PUBLIC_KEY",
    "WEB_PUSH_VAPID_PRIVATE_KEY",
    "WEB_PUSH_VAPID_SUBJECT",
    "ARVAN_S3_ACCESS_KEY",
    "ARVAN_S3_SECRET_KEY",
    "DR_BLOB_OBJECT_ENDPOINT",
    "DR_BLOB_OBJECT_REGION",
    "DR_BLOB_OBJECT_BUCKET",
    "DR_BLOB_OBJECT_PREFIX",
    "DR_BLOB_S3_CREDENTIALS_FILE",
    "DR_BLOB_ENCRYPTION_KEYRING_FILE",
    "DR_SYNC_PEER_URLS_JSON",
    "DR_SYNC_PAIRWISE_KEYS_JSON",
    "WEBAPP_IR_DR_PEERS_JSON",
    "WEBAPP_IR_DR_PAIRWISE_KEYS_JSON",
    "WEBAPP_IR_SHADOW_DR_BIND_ADDRESS",
    "WEBAPP_IR_SHADOW_DR_PORT",
    "WEBAPP_IR_PEER_WEBAPP_FI_IP",
):
    if os.environ.get(_name):
        raise SystemExit("provider credential is present")
if os.environ.get("BACKGROUND_JOBS_ENABLED", "").lower() != "false":
    raise SystemExit("background jobs are not disabled")
_role = os.environ.get("PHYSICAL_SITE", "")
if _role == "bot_fi":
    _prefix = "BOT_FI"
    _app_password = os.environ.get("BOT_APP_DB_PASSWORD", "")
elif _role == "webapp_fi":
    _prefix = "WEBAPP_FI"
    _app_password = os.environ.get("THREE_SITE_APP_DB_PASSWORD", "")
elif _role == "webapp_ir":
    _prefix = "WEBAPP_IR"
    _app_password = os.environ.get("THREE_SITE_APP_DB_PASSWORD", "")
else:
    raise SystemExit("physical site is invalid")
_database = os.environ.get(_prefix + "_POSTGRES_DB", "")
_host = _role + "_db"
if not _app_password or not _database:
    raise SystemExit("application database credential is unavailable")
_user = _role + "_app"
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://" + _user + ":" + _app_password
    + "@" + _host + "/" + _database
)
os.environ["SYNC_DATABASE_URL"] = (
    "postgresql://" + _user + ":" + _app_password
    + "@" + _host + "/" + _database
)
os.environ["POSTGRES_USER"] = _user
os.environ["POSTGRES_PASSWORD"] = _app_password
os.environ["TRADING_BOT_SERVICE"] = "api"

async def _normalize():
    from core.db import init_db, verify_three_site_database_role_bindings
    await init_db()
    await verify_three_site_database_role_bindings()
    from main import (
        _load_runtime_writer_snapshot,
        _run_authorized_startup_mutations,
    )
    _snapshot = await _load_runtime_writer_snapshot()
    await _run_authorized_startup_mutations(_snapshot)

asyncio.run(_normalize())
print(json.dumps({
    "schema": "production-shadow-startup-normalization-invocation-v1",
    "status": "normalized",
    "role": _role,
    "background_jobs_enabled": False,
    "provider_credentials_present": False,
    "provider_network_used": False,
    "redis_started": False,
    "public_service_started": False,
}, sort_keys=True, separators=(",", ":")))
""".strip()


class StartupNormalizationError(RuntimeError):
    """The startup normalization contract failed closed."""


class StartupNormalizationCancellation(StartupNormalizationError):
    """Controller authority disappeared before a permitted step."""


@dataclass(frozen=True)
class LogicalState:
    database_fingerprint_sha256: str
    database_row_count: int
    database_table_count: int
    uploads_tree_sha256: str
    audit_tree_sha256: str
    redis_tree_sha256: str

    def document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "database_fingerprint_sha256": (
                self.database_fingerprint_sha256
            ),
            "database_row_count": self.database_row_count,
            "database_table_count": self.database_table_count,
            "uploads_tree_sha256": self.uploads_tree_sha256,
            "audit_tree_sha256": self.audit_tree_sha256,
            "redis_tree_sha256": self.redis_tree_sha256,
        }
        document["data_state_sha256"] = _sha256(_canonical_json(document))
        return document


@dataclass(frozen=True)
class StopState:
    database_container_id: str
    network_id: str
    operation_owned_running_container_count: int
    oneoff_residue_count: int


@dataclass(frozen=True)
class StartState:
    database_container_id: str
    network_id: str
    database_was_running: bool
    database_start_performed: bool
    oneoff_residue_count: int


class NormalizationBackend(Protocol):
    """Exact-host operations used by the bounded worker."""

    def reconcile_prepared_database_running(self) -> StartState: ...

    def logical_state(self) -> LogicalState: ...

    def normalize_once(self) -> Mapping[str, Any]: ...

    def stop_operation_containers(self) -> StopState: ...


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise StartupNormalizationError(
            "value is not canonical JSON"
        ) from exc


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StartupNormalizationError(
                f"duplicate JSON field: {key}"
            )
        result[key] = value
    return result


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise StartupNormalizationError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _canonical_uuid4(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise StartupNormalizationError(f"{label} is invalid")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise StartupNormalizationError(f"{label} is invalid") from exc
    if str(parsed) != value or parsed.version != 4:
        raise StartupNormalizationError(f"{label} is not canonical UUID4")
    return value


def _parse_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise StartupNormalizationError(f"{label} is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise StartupNormalizationError(
            f"{label} is not canonical UTC"
        ) from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
        != value
    ):
        raise StartupNormalizationError(f"{label} is not canonical UTC")
    return parsed


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _absolute_path(value: Any, *, label: str) -> Path:
    try:
        path = Path(value)
    except TypeError as exc:
        raise StartupNormalizationError(f"{label} is invalid") from exc
    if not path.is_absolute() or ".." in path.parts:
        raise StartupNormalizationError(f"{label} is not absolute")
    return path


def _binding(value: Mapping[str, Any]) -> str:
    unsigned = {
        key: item
        for key, item in value.items()
        if key != "request_binding_sha256"
    }
    return _sha256(_canonical_json(unsigned))


def _validate_inventory_pair(
    request_value: Any,
    response_value: Any,
    *,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validator_request = getattr(
        INVENTORY,
        "validate_prepared_request",
        None,
    )
    validator_response = getattr(
        INVENTORY,
        "validate_prepared_response",
        None,
    )
    if not callable(validator_request) or not callable(validator_response):
        raise StartupNormalizationError(
            "prepared inventory validator is unavailable"
        )
    try:
        inventory_issued_at = _parse_timestamp(
            request_value.get("issued_at"),
            label="prepared inventory issued_at",
        )
        inventory_captured_at = _parse_timestamp(
            response_value.get("captured_at"),
            label="prepared inventory captured_at",
        )
        if (
            inventory_issued_at
            > inventory_captured_at
            or inventory_captured_at
            > now + timedelta(seconds=FUTURE_SKEW_SECONDS)
        ):
            raise StartupNormalizationError(
                "prepared inventory chronology is invalid"
            )
        inventory_request = validator_request(
            request_value,
            now=inventory_issued_at,
        )
        inventory_response = validator_response(
            response_value,
            request=inventory_request,
            now=inventory_captured_at,
        )
    except Exception as exc:
        raise StartupNormalizationError(
            "historically valid prepared inventory proof is invalid"
        ) from exc
    if (
        inventory_request.get("expected_database_state")
        not in {None, "running-healthy"}
        or inventory_response.get("prepared_database_running") is not True
        or inventory_response.get("prepared_database_healthy") is not True
        or inventory_response.get("operation_resource_counts")
        != {
            "container": 1,
            "network": 1,
            "volume": 0,
            "image": 0,
        }
        or inventory_response.get("stable_capture_count") != 2
        or inventory_response.get("descriptors_returned") is not False
        or inventory_response.get("environment_values_returned") is not False
        or inventory_response.get("path_descriptors_returned") is not False
        or inventory_response.get("docker_read_only") is not True
        or inventory_response.get("network_io_performed") is not False
        or inventory_response.get("filesystem_mutated") is not False
    ):
        raise StartupNormalizationError(
            "prepared inventory does not prove the running clone closure"
        )
    return inventory_request, inventory_response


def build_request(
    *,
    campaign_id: str,
    operation_id: str,
    release_sha: str,
    release_tree_sha: str,
    role: str,
    worker_sha256: str,
    inventory_agent_sha256: str,
    contract_worker_sha256: str,
    role_manifest_path: Path,
    role_manifest_sha256: str,
    pre_inventory_request: Mapping[str, Any],
    pre_inventory_response: Mapping[str, Any],
    controller_challenge_sha256: str,
    issued_at: datetime,
    expires_at: datetime,
) -> dict[str, Any]:
    release_root = (
        INVENTORY.PROJECT_ROOT_PREFIX
        / operation_id
        / "releases"
        / release_sha
    )
    document: dict[str, Any] = {
        "schema": REQUEST_SCHEMA,
        "status": "authorized-request",
        "campaign_id": campaign_id,
        "operation_id": operation_id,
        "release_sha": release_sha,
        "release_tree_sha": release_tree_sha,
        "role": role,
        "expected_host": ROLE_HOSTS.get(role),
        "controller_challenge_sha256": controller_challenge_sha256,
        "issued_at": _timestamp(issued_at),
        "expires_at": _timestamp(expires_at),
        "worker_path": str(release_root / WORKER_RELATIVE),
        "worker_sha256": worker_sha256,
        "inventory_agent_sha256": inventory_agent_sha256,
        "contract_worker_sha256": contract_worker_sha256,
        "role_manifest_path": str(role_manifest_path),
        "role_manifest_sha256": role_manifest_sha256,
        "pre_inventory_request": dict(pre_inventory_request),
        "pre_inventory_response": dict(pre_inventory_response),
        "constraints": dict(EXPECTED_CONSTRAINTS),
        "request_binding_sha256": ZERO_SHA256,
    }
    document["request_binding_sha256"] = _binding(document)
    return validate_request(document, now=issued_at)


def validate_request(
    value: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value) != REQUEST_FIELDS
        or value.get("schema") != REQUEST_SCHEMA
        or value.get("status") != "authorized-request"
        or value.get("role") not in ROLE_ORDER
    ):
        raise StartupNormalizationError(
            "normalization request fields are not exact"
        )
    try:
        document = json.loads(
            _canonical_json(dict(value)).decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise StartupNormalizationError(
            "normalization request is not JSON-compatible"
        ) from exc
    if len(_canonical_json(document)) > MAX_CONTROL_BYTES:
        raise StartupNormalizationError(
            "normalization request exceeds its bound"
        )
    campaign_id = _canonical_uuid4(
        document["campaign_id"],
        label="campaign ID",
    )
    operation_id = _canonical_uuid4(
        document["operation_id"],
        label="operation ID",
    )
    if campaign_id == operation_id:
        raise StartupNormalizationError(
            "campaign and operation IDs must differ"
        )
    role = document["role"]
    if (
        not isinstance(document["release_sha"], str)
        or SHA40_RE.fullmatch(document["release_sha"]) is None
        or not isinstance(document["release_tree_sha"], str)
        or SHA40_RE.fullmatch(document["release_tree_sha"]) is None
        or document["expected_host"] != ROLE_HOSTS[role]
        or document["constraints"] != EXPECTED_CONSTRAINTS
    ):
        raise StartupNormalizationError(
            "normalization release, host, or constraints differ"
        )
    for field in (
        "controller_challenge_sha256",
        "worker_sha256",
        "inventory_agent_sha256",
        "contract_worker_sha256",
        "role_manifest_sha256",
        "request_binding_sha256",
    ):
        _nonzero_sha256(document[field], label=field)
    issued_at = _parse_timestamp(document["issued_at"], label="issued_at")
    expires_at = _parse_timestamp(
        document["expires_at"],
        label="expires_at",
    )
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    lifetime = (expires_at - issued_at).total_seconds()
    if (
        not MIN_LIFETIME_SECONDS <= lifetime <= MAX_LIFETIME_SECONDS
        or issued_at
        > observed_now + timedelta(seconds=FUTURE_SKEW_SECONDS)
        or observed_now > expires_at
    ):
        raise StartupNormalizationError(
            "normalization request is stale or outside its time bound"
        )
    release_root = (
        INVENTORY.PROJECT_ROOT_PREFIX
        / operation_id
        / "releases"
        / document["release_sha"]
    )
    if (
        _absolute_path(document["worker_path"], label="worker path")
        != release_root / WORKER_RELATIVE
    ):
        raise StartupNormalizationError(
            "normalization worker path is not operation-derived"
        )
    manifest_path = _absolute_path(
        document["role_manifest_path"],
        label="role manifest path",
    )
    try:
        expected_manifest = INVENTORY._prepared_manifest_path(  # noqa: SLF001
            operation_id=operation_id,
            role=role,
            contract_kind=(
                "wa-ir-operation"
                if role == "webapp_ir"
                else "finland-precommit"
            ),
        )
    except Exception as exc:
        raise StartupNormalizationError(
            "role manifest contract cannot be derived"
        ) from exc
    if manifest_path != expected_manifest:
        raise StartupNormalizationError(
            "role manifest path is not operation-derived"
        )
    inventory_request, inventory_response = _validate_inventory_pair(
        document["pre_inventory_request"],
        document["pre_inventory_response"],
        now=observed_now,
    )
    identity_fields = (
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "expected_host",
        "role_manifest_sha256",
    )
    if any(
        inventory_request.get(field) != document[field]
        for field in identity_fields
    ):
        raise StartupNormalizationError(
            "prepared inventory identity differs from normalization"
        )
    if (
        inventory_request.get("agent_sha256")
        != document["inventory_agent_sha256"]
        or inventory_request.get("contract_worker_sha256")
        != document["contract_worker_sha256"]
        or inventory_request.get("controller_challenge_sha256")
        == document["controller_challenge_sha256"]
    ):
        raise StartupNormalizationError(
            "normalization and inventory challenge bindings are invalid"
        )
    if document["request_binding_sha256"] != _binding(document):
        raise StartupNormalizationError(
            "normalization request binding SHA-256 differs"
        )
    return document


def _validate_state(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != STATE_FIELDS:
        raise StartupNormalizationError(f"{label} fields are not exact")
    document = dict(value)
    for field in (
        "database_fingerprint_sha256",
        "uploads_tree_sha256",
        "audit_tree_sha256",
        "redis_tree_sha256",
        "data_state_sha256",
    ):
        _nonzero_sha256(document[field], label=f"{label} {field}")
    if (
        isinstance(document["database_row_count"], bool)
        or not isinstance(document["database_row_count"], int)
        or not 0 <= document["database_row_count"] <= 10**15
        or isinstance(document["database_table_count"], bool)
        or not isinstance(document["database_table_count"], int)
        or not 1 <= document["database_table_count"] <= 100_000
    ):
        raise StartupNormalizationError(f"{label} counts are invalid")
    unsigned = {
        key: item
        for key, item in document.items()
        if key != "data_state_sha256"
    }
    if document["data_state_sha256"] != _sha256(_canonical_json(unsigned)):
        raise StartupNormalizationError(f"{label} digest differs")
    return document


def validate_result(
    value: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise StartupNormalizationError(
            "normalization result request is invalid"
        )
    request_issued_at = _parse_timestamp(
        request.get("issued_at"),
        label="request issued_at",
    )
    # A persisted result remains bound to the request structure using the
    # time at which that request was issued.  Freshness at execution is
    # enforced by execute(); current observation is checked independently
    # against the longer normalization expiry below.
    bound_request = validate_request(request, now=request_issued_at)
    if not isinstance(value, Mapping) or set(value) != RESULT_FIELDS:
        raise StartupNormalizationError(
            "normalization result fields are not exact"
        )
    document = json.loads(_canonical_json(dict(value)).decode("ascii"))
    identity_fields = (
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "role",
        "expected_host",
        "controller_challenge_sha256",
        "issued_at",
        "expires_at",
        "request_binding_sha256",
    )
    if (
        document["schema"] != RESULT_SCHEMA
        or document["status"] != "normalized-stopped"
        or any(document[field] != bound_request[field] for field in identity_fields)
    ):
        raise StartupNormalizationError(
            "normalization result identity differs"
        )
    pre = bound_request["pre_inventory_response"]
    if (
        document["pre_inventory_response_sha256"]
        != pre["response_sha256"]
        or document["prepared_container_id"]
        != pre["prepared_container_id"]
        or document["prepared_network_id"] != pre["prepared_network_id"]
    ):
        raise StartupNormalizationError(
            "normalization result prepared binding differs"
        )
    for field in (
        "prepared_container_identity_sha256",
        "prepared_container_metadata_sha256",
        "prepared_network_identity_sha256",
        "prepared_network_metadata_sha256",
        "prepared_config_sha256",
        "prepared_environment_sha256",
        "prepared_environment_entry_count",
        "prepared_compose_config_sha256",
        "prepared_host_config_sha256",
        "prepared_mounts_sha256",
        "prepared_network_attachment_sha256",
    ):
        if document[field] != pre[field]:
            raise StartupNormalizationError(
                f"normalization result {field} differs"
            )
    before = _validate_state(document["before_state"], label="before state")
    first = _validate_state(
        document["first_invocation_state"],
        label="first invocation state",
    )
    second = _validate_state(
        document["second_invocation_state"],
        label="second invocation state",
    )
    if first != second:
        raise StartupNormalizationError(
            "second startup invocation changed database or data"
        )
    exact_values = {
        "normalization_invocation_count": 2,
        "second_start_database_delta_count": 0,
        "second_start_data_delta_count": 0,
        "provider_credentials_present": False,
        "provider_network_used": False,
        "background_jobs_enabled": False,
        "redis_started": False,
        "public_service_started": False,
        "oneoff_residue_count": 0,
        "database_stop_performed": True,
        "operation_owned_running_container_count": 0,
        "persistent_resource_removed": False,
        "legacy_mutated": False,
        "current_mutated": False,
        "volume_mutated": False,
        "object_storage_used": False,
    }
    if any(document[key] != item for key, item in exact_values.items()):
        raise StartupNormalizationError(
            "normalization result safety closure differs"
        )
    if (
        type(document["database_was_running_at_reconciliation"])
        is not bool
        or type(document["database_start_performed"]) is not bool
        or (
            document["database_was_running_at_reconciliation"]
            is document["database_start_performed"]
        )
    ):
        raise StartupNormalizationError(
            "normalization result database start closure differs"
        )
    _nonzero_sha256(
        document["normalization_command_sha256"],
        label="normalization command",
    )
    captured_at = _parse_timestamp(
        document["captured_at"],
        label="captured_at",
    )
    completed_at = _parse_timestamp(
        document["completed_at"],
        label="completed_at",
    )
    issued_at = _parse_timestamp(document["issued_at"], label="issued_at")
    expires_at = _parse_timestamp(
        document["expires_at"],
        label="expires_at",
    )
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    for parsed in (captured_at, completed_at):
        if not (
            issued_at <= parsed <= expires_at
        ):
            raise StartupNormalizationError(
                "normalization capture time is outside the request window"
            )
    if captured_at > completed_at or observed_now > expires_at:
        raise StartupNormalizationError(
            "normalization chronology or observation time is invalid"
        )
    unsigned = {
        key: item
        for key, item in document.items()
        if key != "response_sha256"
    }
    if document["response_sha256"] != _sha256(_canonical_json(unsigned)):
        raise StartupNormalizationError(
            "normalization response SHA-256 differs"
        )
    _nonzero_sha256(document["response_sha256"], label="response")
    del before
    return document


def _secure_regular_bytes(
    path: Path,
    *,
    label: str,
    maximum: int,
    exact_mode: int | None = None,
) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_gid != 0
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > maximum
            or (
                stat.S_IMODE(before.st_mode) != exact_mode
                if exact_mode is not None
                else bool(stat.S_IMODE(before.st_mode) & 0o022)
            )
        ):
            raise StartupNormalizationError(
                f"{label} file identity is unsafe"
            )
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise StartupNormalizationError(
            f"{label} is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    stable = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_uid",
        "st_gid",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if (
        len(payload) != before.st_size
        or len(payload) > maximum
        or any(
            getattr(before, field) != getattr(after, field)
            for field in stable
        )
    ):
        raise StartupNormalizationError(
            f"{label} changed while being read"
        )
    return payload


def _read_manifest_sha256(path: Path) -> str:
    return _sha256(
        _secure_regular_bytes(
            path,
            label="role manifest",
            maximum=MAX_CONTROL_BYTES,
            exact_mode=0o600,
        )
    )


def _empty_tree_sha256(path: Path, *, label: str) -> str:
    try:
        metadata = path.stat(follow_symlinks=False)
        entries = list(path.iterdir())
    except OSError as exc:
        raise StartupNormalizationError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or entries
    ):
        raise StartupNormalizationError(f"{label} is not pristine")
    return _sha256(
        _canonical_json(
            {
                "schema": "production-shadow-pristine-directory-v1",
                "entry_count": 0,
            }
        )
    )


def _normalization_output(value: str, *, role: str) -> dict[str, Any]:
    try:
        document = json.loads(value, object_pairs_hook=_strict_object)
    except (ValueError, json.JSONDecodeError) as exc:
        raise StartupNormalizationError(
            "startup normalization output is invalid"
        ) from exc
    expected = {
        "schema": "production-shadow-startup-normalization-invocation-v1",
        "status": "normalized",
        "role": role,
        "background_jobs_enabled": False,
        "provider_credentials_present": False,
        "provider_network_used": False,
        "redis_started": False,
        "public_service_started": False,
    }
    if document != expected:
        raise StartupNormalizationError(
            "startup normalization invocation did not prove its closure"
        )
    return document


class ExactReleaseBackend:
    """Use only exact-release role helpers and the local Docker socket."""

    def __init__(self, request: Mapping[str, Any]) -> None:
        self.request = dict(request)
        self.role = str(request["role"])
        self.manifest_path = Path(str(request["role_manifest_path"]))
        if (
            _read_manifest_sha256(self.manifest_path)
            != request["role_manifest_sha256"]
        ):
            raise StartupNormalizationError(
                "role manifest bytes differ from the request"
            )
        if self.role == "webapp_ir":
            self.manifest = WA_OPERATION.load_manifest(
                self.manifest_path,
                required_uid=0,
            )
            self.paths = WA_OPERATION._canonical_operation_paths(  # noqa: SLF001
                self.manifest
            )
            self.runtime_env = WA_OPERATION.parse_safe_dotenv(
                _secure_regular_bytes(
                    self.paths.runtime_env,
                    label="WA-IR role environment",
                    maximum=MAX_CONTROL_BYTES,
                    exact_mode=0o600,
                )
            )
            WA_OPERATION._validate_compose_config(  # noqa: SLF001
                self.manifest,
                operation_root=self.paths.project_root,
            )
            self.prefix = WA_OPERATION._compose_base(  # noqa: SLF001
                self.manifest,
                operation_root=self.paths.project_root,
            )
            self.services = self.manifest.services
            self.profile = "webapp-ir-prepare"
        else:
            self.manifest = PRECOMMIT.load_manifest(self.manifest_path)
            self.paths = PRECOMMIT.operation_paths(
                self.manifest.operation_id,
                self.manifest.release_sha,
                self.manifest.role,
            )
            self.runtime_env = PRECOMMIT._verify_role_material(  # noqa: SLF001
                self.manifest,
                self.paths,
            )
            PRECOMMIT._verify_compose(  # noqa: SLF001
                self.manifest,
                self.paths,
            )
            self.services = PRECOMMIT.ROLE_SERVICES[self.role]
            self.profile = f"{self.services['profile']}-prepare"
        if (
            self.manifest.operation_id != request["operation_id"]
            or self.manifest.release_sha != request["release_sha"]
            or self.manifest.release_tree_sha != request["release_tree_sha"]
            or (
                hasattr(self.manifest, "role")
                and self.manifest.role != self.role
            )
        ):
            raise StartupNormalizationError(
                "role manifest identity differs from the request"
            )
        self._validate_normalization_service()

    def _validate_normalization_service(self) -> None:
        """Prove the overridden one-off stays provider-free and internal."""

        if self.role == "webapp_ir":
            # The exact-release validator compares every resolved environment
            # name/value, mount, image, profile and network for this service.
            config = WA_OPERATION._validate_compose_config(  # noqa: SLF001
                self.manifest,
                operation_root=self.paths.project_root,
            )
            if not isinstance(config, Mapping):
                raise StartupNormalizationError(
                    "WA-IR resolved Compose closure is invalid"
                )
        else:
            try:
                raw = PRECOMMIT._run(  # noqa: SLF001
                    [
                        *PRECOMMIT._compose_base(self.manifest, self.paths),  # noqa: SLF001
                        "--profile",
                        self.profile,
                        "config",
                        "--format",
                        "json",
                    ],
                    timeout=60,
                )
                config = json.loads(raw, object_pairs_hook=_strict_object)
            except Exception as exc:
                raise StartupNormalizationError(
                    "resolved normalization Compose cannot be inspected"
                ) from exc
        services = config.get("services") if isinstance(config, dict) else None
        networks = config.get("networks") if isinstance(config, dict) else None
        normalization_service = (
            self.services["roles"]
            if self.role == "webapp_ir"
            else self.services["migration"]
        )
        service = (
            services.get(str(normalization_service))
            if isinstance(services, dict)
            else None
        )
        environment = (
            service.get("environment")
            if isinstance(service, dict)
            else None
        )
        service_networks = (
            service.get("networks")
            if isinstance(service, dict)
            else None
        )
        network_key = self.role
        network = (
            networks.get(network_key)
            if isinstance(networks, dict)
            else None
        )
        network_names = (
            set(service_networks)
            if isinstance(service_networks, (dict, list))
            else set()
        )
        service_volumes = (
            service.get("volumes")
            if isinstance(service, dict)
            else None
        )
        if self.role == "webapp_ir":
            expected_service = WA_OPERATION.EXPECTED_SERVICES["roles"]
            expected_password = self.runtime_env.get(
                "WEBAPP_IR_APP_DB_PASSWORD",
                "",
            )
            wa_service_closure_is_exact = (
                normalization_service == expected_service
                and isinstance(environment, dict)
                and set(environment) == WA_ROLE_NORMALIZATION_ENV_FIELDS
                and bool(expected_password)
                and environment.get("THREE_SITE_APP_DB_PASSWORD")
                == expected_password
                and service.get("restart") == "no"
                and service.get("pull_policy") == "never"
                and service.get("profiles") == ["webapp-ir-prepare"]
                and isinstance(service_volumes, list)
                and len(service_volumes) == 1
                and isinstance(service_volumes[0], dict)
                and service_volumes[0].get("type") == "bind"
                and service_volumes[0].get("source") == str(self.paths.ca)
                and service_volumes[0].get("target")
                == "/run/production-dr-ca/ca.crt"
                and service_volumes[0].get("read_only") is True
            )
        else:
            wa_service_closure_is_exact = True
        if (
            not isinstance(service, dict)
            or service.get("image")
            != (
                self.manifest.image_artifacts["app"].config_digest
                if self.role == "webapp_ir"
                else self.manifest.runtime_image_ids["app"]
            )
            or "build" in service
            or service.get("ports") not in (None, [])
            or service.get("network_mode") is not None
            or service.get("env_file") is not None
            or service.get("extra_hosts") is not None
            or network_names != {network_key}
            or not isinstance(network, dict)
            or network.get("internal") is not True
            or network.get("external") not in {None, False}
            or not isinstance(environment, dict)
            or environment.get("BACKGROUND_JOBS_ENABLED") != "false"
            or environment.get("PHYSICAL_SITE") != self.role
            or environment.get("LOGICAL_AUTHORITY")
            != ("foreign" if self.role == "bot_fi" else "webapp")
            or environment.get("THREE_SITE_DR_ENABLED") != "true"
            or environment.get("DR_EVENT_PROTOCOL_ENABLED") != "true"
            or environment.get("DR_EVENT_PROTOCOL_STRICT") != "true"
            or environment.get("RELEASE_SHA") != self.manifest.release_sha
            or not isinstance(environment.get("DATABASE_URL"), str)
            or not isinstance(environment.get("SYNC_DATABASE_URL"), str)
            or not isinstance(environment.get("JWT_SECRET_KEY"), str)
            or not wa_service_closure_is_exact
            or any(environment.get(name) for name in FORBIDDEN_PROVIDER_ENV)
            or any(name in environment for name in FORBIDDEN_PROVIDER_ENV)
        ):
            raise StartupNormalizationError(
                "normalization one-off is not exact, internal, and provider-free"
            )

    def _database_id(self) -> str:
        if self.role == "webapp_ir":
            output = WA_OPERATION._run(  # noqa: SLF001
                [
                    *self.prefix,
                    "ps",
                    "--all",
                    "--quiet",
                    self.services["database"],
                ],
                timeout=30,
            )
        else:
            output = PRECOMMIT._database_container(  # noqa: SLF001
                self.manifest,
                self.paths,
            )
        if CONTAINER_ID_RE.fullmatch(output) is None:
            raise StartupNormalizationError(
                "operation database container is unavailable"
            )
        return output

    def _network_id(self, *, database_id: str, attached: bool) -> str:
        if self.role == "webapp_ir":
            evidence = WA_OPERATION._validate_operation_network(  # noqa: SLF001
                self.manifest,
                expected_container_id=database_id,
                require_present=True,
                require_attached=attached,
            )
            identifier = (
                evidence.get("network_id")
                if isinstance(evidence, Mapping)
                else None
            )
        else:
            identifier = PRECOMMIT._network_identifier(  # noqa: SLF001
                self.manifest,
                self.paths,
            )
            if attached:
                PRECOMMIT._validate_network(  # noqa: SLF001
                    identifier,
                    self.manifest,
                    self.paths,
                    allowed_container_ids=frozenset({database_id}),
                )
            else:
                try:
                    PRECOMMIT._validate_network(  # noqa: SLF001
                        identifier,
                        self.manifest,
                        self.paths,
                        allowed_container_ids=frozenset(),
                    )
                except PRECOMMIT.PrecommitWorkerError:
                    # Docker versions differ on whether a stopped container
                    # remains listed as a network endpoint.  Both closures are
                    # safe when the sole permitted ID is the bound database.
                    PRECOMMIT._validate_network(  # noqa: SLF001
                        identifier,
                        self.manifest,
                        self.paths,
                        allowed_container_ids=frozenset({database_id}),
                    )
        if not isinstance(identifier, str) or NETWORK_ID_RE.fullmatch(identifier) is None:
            raise StartupNormalizationError(
                "operation internal network identity is invalid"
            )
        return identifier

    def _validated_database_running(self, identifier: str) -> bool:
        if self.role == "webapp_ir":
            running = WA_OPERATION._validate_database_container(  # noqa: SLF001
                identifier,
                self.manifest,
            )
        else:
            evidence = PRECOMMIT._validate_database_container(  # noqa: SLF001
                identifier,
                self.manifest,
                self.paths,
                require_running=None,
            )
            running = evidence["running"]
        if type(running) is not bool:
            raise StartupNormalizationError(
                "operation database running state is invalid"
            )
        return running

    def _database_healthy(self, identifier: str) -> bool:
        arguments = (
            [*WA_OPERATION.DOCKER_BASE, "inspect", identifier]
            if self.role == "webapp_ir"
            else [PRECOMMIT.DOCKER, "inspect", identifier]
        )
        raw = (
            WA_OPERATION._run(  # noqa: SLF001
                arguments,
                timeout=30,
            )
            if self.role == "webapp_ir"
            else PRECOMMIT._run(  # noqa: SLF001
                arguments,
                timeout=30,
            )
        )
        try:
            payload = json.loads(raw, object_pairs_hook=_strict_object)
        except (ValueError, json.JSONDecodeError) as exc:
            raise StartupNormalizationError(
                "operation database health inspection is invalid"
            ) from exc
        if (
            not isinstance(payload, list)
            or len(payload) != 1
            or not isinstance(payload[0], dict)
            or payload[0].get("Id") != identifier
        ):
            raise StartupNormalizationError(
                "operation database health identity differs"
            )
        state = payload[0].get("State")
        health = state.get("Health") if isinstance(state, dict) else None
        status = health.get("Status") if isinstance(health, dict) else None
        if (
            not isinstance(state, dict)
            or state.get("Running") is not True
            or status not in {"starting", "healthy", "unhealthy"}
        ):
            raise StartupNormalizationError(
                "operation database health state is invalid"
            )
        return status == "healthy"

    def _ensure_running_database(self) -> tuple[str, str]:
        identifier = self._database_id()
        if self._validated_database_running(identifier) is not True:
            raise StartupNormalizationError(
                "operation database is not running"
            )
        return identifier, self._network_id(
            database_id=identifier,
            attached=True,
        )

    def _bound_database_state(self) -> tuple[str, str, bool]:
        identifier = self._database_id()
        expected_container = self.request["pre_inventory_response"][
            "prepared_container_id"
        ]
        expected_network = self.request["pre_inventory_response"][
            "prepared_network_id"
        ]
        if identifier != expected_container:
            raise StartupNormalizationError(
                "database container differs from prepared identity"
            )
        running = self._validated_database_running(identifier)
        network_id = self._network_id(
            database_id=identifier,
            attached=running,
        )
        if network_id != expected_network:
            raise StartupNormalizationError(
                "database network differs from prepared identity"
            )
        return identifier, network_id, running

    def reconcile_prepared_database_running(self) -> StartState:
        """Start only the exact request-bound DB after a lost-result retry."""

        identifier, expected_network, running = (
            self._bound_database_state()
        )
        residue = self._oneoff_ids()
        if residue:
            raise StartupNormalizationError(
                "operation has one-off residue before database reconciliation"
            )
        started = False
        if not running:
            arguments = (
                [*WA_OPERATION.DOCKER_BASE, "start", identifier]
                if self.role == "webapp_ir"
                else [PRECOMMIT.DOCKER, "start", identifier]
            )
            if self.role == "webapp_ir":
                WA_OPERATION._run(arguments, timeout=300)  # noqa: SLF001
            else:
                PRECOMMIT._run(arguments, timeout=300)  # noqa: SLF001
            started = True
        healthy = False
        for attempt in range(120):
            if (
                self._validated_database_running(identifier)
                and self._database_healthy(identifier)
            ):
                healthy = True
                break
            if attempt < 119:
                time.sleep(0.25)
        if not healthy:
            raise StartupNormalizationError(
                "exact operation database did not become healthy"
            )
        if (
            self._database_id() != identifier
            or self._network_id(
                database_id=identifier,
                attached=True,
            )
            != expected_network
            or self._oneoff_ids()
        ):
            raise StartupNormalizationError(
                "operation database changed during start reconciliation"
            )
        return StartState(
            database_container_id=identifier,
            network_id=expected_network,
            database_was_running=running,
            database_start_performed=started,
            oneoff_residue_count=0,
        )

    def _oneoff_ids(self) -> list[str]:
        if self.role == "webapp_ir":
            return WA_OPERATION._oneoff_ids(  # noqa: SLF001
                self.manifest,
                operation_root=self.paths.project_root,
            )
        return PRECOMMIT._operation_non_database_containers(  # noqa: SLF001
            self.manifest,
            self.paths,
        )

    def logical_state(self) -> LogicalState:
        self._ensure_running_database()
        if self._oneoff_ids():
            raise StartupNormalizationError(
                "operation has one-off residue before attestation"
            )
        if self.role == "webapp_ir":
            fingerprint = WA_OPERATION._database_fingerprint(  # noqa: SLF001
                self.prefix,
                self.manifest,
            )
            uploads = WA_OPERATION._attest_extracted_archive_tree(  # noqa: SLF001
                self.paths.restore_dump.parent / "uploads.tar.gz",
                self.paths.uploads,
                mode="r:gz",
                required_uid=0,
            )["tree_sha256"]
            audit = WA_OPERATION._attest_extracted_archive_tree(  # noqa: SLF001
                self.paths.restore_dump.parent / "audit.tar.gz",
                self.paths.audit,
                mode="r:gz",
                required_uid=0,
            )["tree_sha256"]
            redis = _empty_tree_sha256(
                self.paths.redis,
                label="WA-IR Redis directory",
            )
        else:
            fingerprint = PRECOMMIT._database_fingerprint(  # noqa: SLF001
                self.manifest,
                self.paths,
            )
            uploads = PRECOMMIT._tree_digest(  # noqa: SLF001
                self.manifest,
                self.paths,
                "uploads",
            )
            audit = PRECOMMIT._tree_digest(  # noqa: SLF001
                self.manifest,
                self.paths,
                "audit",
            )
            redis = _empty_tree_sha256(
                self.paths.data_root / self.role.replace("_", "-") / "redis",
                label=f"{self.role} Redis directory",
            )
        if self._oneoff_ids():
            raise StartupNormalizationError(
                "logical attestation left one-off residue"
            )
        return LogicalState(
            database_fingerprint_sha256=fingerprint[0],
            database_row_count=fingerprint[1],
            database_table_count=fingerprint[2],
            uploads_tree_sha256=str(uploads),
            audit_tree_sha256=str(audit),
            redis_tree_sha256=redis,
        )

    def normalize_once(self) -> Mapping[str, Any]:
        self._ensure_running_database()
        command = ["python", "-c", NORMALIZATION_PROGRAM]
        password_name = (
            "BOT_APP_DB_PASSWORD"
            if self.role == "bot_fi"
            else "THREE_SITE_APP_DB_PASSWORD"
        )
        source_name = {
            "bot_fi": "BOT_FI_APP_DB_PASSWORD",
            "webapp_fi": "WEBAPP_FI_APP_DB_PASSWORD",
            "webapp_ir": "WEBAPP_IR_APP_DB_PASSWORD",
        }[self.role]
        password = self.runtime_env.get(source_name, "")
        if not password:
            raise StartupNormalizationError(
                "application database password is unavailable"
            )
        if self._oneoff_ids():
            raise StartupNormalizationError(
                "operation has stale one-off residue"
            )
        if self.role == "webapp_ir":
            arguments = [
                *self.prefix,
                "--profile",
                self.profile,
                "run",
                "--rm",
                "--no-deps",
                "--label",
                (
                    "trading-bot.production.operation-id="
                    f"{self.manifest.operation_id}"
                ),
                "-T",
                str(self.services["roles"]),
                *command,
            ]
            try:
                output = WA_OPERATION._run(  # noqa: SLF001
                    arguments,
                    timeout=900,
                    env=WA_OPERATION._SAFE_ENV,  # noqa: SLF001
                )
            finally:
                with WA_OPERATION._late_reconciliation_scope():  # noqa: SLF001
                    WA_OPERATION._cleanup_operation_oneoffs(  # noqa: SLF001
                        self.manifest,
                        operation_root=self.paths.project_root,
                    )
        else:
            arguments = [
                *PRECOMMIT._compose_base(self.manifest, self.paths),  # noqa: SLF001
                "--profile",
                self.profile,
                "run",
                "--rm",
                "--no-deps",
                "--label",
                (
                    "trading-bot.production.operation-id="
                    f"{self.manifest.operation_id}"
                ),
                "-T",
                "--env",
                password_name,
                str(self.services["migration"]),
                *command,
            ]
            command_env = {
                **PRECOMMIT._SAFE_ENV,  # noqa: SLF001
                password_name: password,
            }
            try:
                output = PRECOMMIT._run(  # noqa: SLF001
                    arguments,
                    timeout=900,
                    env=command_env,
                )
            finally:
                PRECOMMIT._cleanup_oneoffs(  # noqa: SLF001
                    self.manifest,
                    self.paths,
                )
        if self._oneoff_ids():
            raise StartupNormalizationError(
                "normalization left one-off residue"
            )
        return _normalization_output(output, role=self.role)

    def stop_operation_containers(self) -> StopState:
        service = str(self.services["database"])
        identifier, expected_network, _was_running = (
            self._bound_database_state()
        )
        if self.role == "webapp_ir":
            with WA_OPERATION._late_reconciliation_scope():  # noqa: SLF001
                WA_OPERATION._cleanup_operation_oneoffs(  # noqa: SLF001
                    self.manifest,
                    operation_root=self.paths.project_root,
                )
                WA_OPERATION._run(  # noqa: SLF001
                    [*self.prefix, "stop", "--timeout", "30", service],
                    timeout=60,
                )
                running = WA_OPERATION._validate_database_container(  # noqa: SLF001
                    identifier,
                    self.manifest,
                )
                network_id = self._network_id(
                    database_id=identifier,
                    attached=running,
                )
        else:
            PRECOMMIT._cleanup_oneoffs(  # noqa: SLF001
                self.manifest,
                self.paths,
            )
            PRECOMMIT._run(  # noqa: SLF001
                [
                    *PRECOMMIT._compose_base(self.manifest, self.paths),  # noqa: SLF001
                    "stop",
                    "--timeout",
                    "30",
                    service,
                ],
                timeout=60,
            )
            evidence = PRECOMMIT._validate_database_container(  # noqa: SLF001
                identifier,
                self.manifest,
                self.paths,
                require_running=False,
            )
            running = evidence["running"]
            network_id = self._network_id(
                database_id=identifier,
                attached=False,
            )
        residue = self._oneoff_ids()
        if running or residue or network_id != expected_network:
            raise StartupNormalizationError(
                "operation containers did not reach the stopped closure"
            )
        return StopState(
            database_container_id=identifier,
            network_id=network_id,
            operation_owned_running_container_count=0,
            oneoff_residue_count=0,
        )


def _verify_execution_context(request: Mapping[str, Any]) -> None:
    if (
        os.geteuid() != 0
        or os.getegid() != 0
        or Path(__file__).resolve() != Path(request["worker_path"])
    ):
        raise StartupNormalizationError(
            "normalization worker requires its root exact-release context"
        )
    release_root = Path(request["worker_path"]).parents[1]
    expected_inventory_path = release_root / INVENTORY_RELATIVE
    expected_contract_path = (
        release_root / CONTRACT_WORKER_RELATIVES[request["role"]]
    )
    if (
        Path(str(getattr(INVENTORY, "__file__", ""))).resolve()
        != expected_inventory_path
        or (
            request["role"] == "webapp_ir"
            and Path(str(getattr(WA_OPERATION, "__file__", ""))).resolve()
            != expected_contract_path
        )
        or (
            request["role"] != "webapp_ir"
            and Path(str(getattr(PRECOMMIT, "__file__", ""))).resolve()
            != expected_contract_path
        )
    ):
        raise StartupNormalizationError(
            "loaded exact-release dependency origin differs"
        )
    expected_paths = {
        Path(request["worker_path"]): request["worker_sha256"],
        expected_inventory_path: (
            request["inventory_agent_sha256"]
        ),
        expected_contract_path: (
            request["contract_worker_sha256"]
        ),
    }
    for path, expected in expected_paths.items():
        payload = _secure_regular_bytes(
            path,
            label="exact-release worker dependency",
            maximum=MAX_RELEASE_FILE_BYTES,
        )
        if _sha256(payload) != expected:
            raise StartupNormalizationError(
                "exact-release worker dependency identity differs"
            )
    commands = (
        (
            [
                "/usr/bin/git",
                "-C",
                str(release_root),
                "rev-parse",
                "HEAD^{commit}",
            ],
            request["release_sha"],
        ),
        (
            [
                "/usr/bin/git",
                "-C",
                str(release_root),
                "rev-parse",
                "HEAD^{tree}",
            ],
            request["release_tree_sha"],
        ),
        (
            [
                "/usr/bin/git",
                "-C",
                str(release_root),
                "branch",
                "--show-current",
            ],
            "",
        ),
        (
            [
                "/usr/bin/git",
                "-C",
                str(release_root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ],
            "",
        ),
        (
            [
                "/usr/bin/git",
                "-C",
                str(release_root),
                "ls-files",
                "--others",
                "--exclude-standard",
            ],
            "",
        ),
        (
            [
                "/usr/bin/git",
                "-C",
                str(release_root),
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
            ],
            "",
        ),
    )
    for command, expected in commands:
        try:
            observed = WA_OPERATION._run(  # noqa: SLF001
                command,
                timeout=30,
                env=WA_OPERATION._SAFE_GIT_ENV,  # noqa: SLF001
            )
        except BaseException as exc:
            raise StartupNormalizationError(
                "immutable release verification failed"
            ) from exc
        if observed != expected:
            raise StartupNormalizationError(
                "normalization worker is not in the immutable clean release"
            )
    try:
        INVENTORY._verify_git_index_visibility(release_root)  # noqa: SLF001
    except Exception as exc:
        raise StartupNormalizationError(
            "immutable release index visibility differs"
        ) from exc
    for relative in (
        WORKER_RELATIVE,
        INVENTORY_RELATIVE,
        CONTRACT_WORKER_RELATIVES[request["role"]],
    ):
        try:
            tracked = WA_OPERATION._run(  # noqa: SLF001
                [
                    "/usr/bin/git",
                    "-C",
                    str(release_root),
                    "ls-files",
                    "--stage",
                    "--",
                    str(relative),
                ],
                timeout=30,
                env=WA_OPERATION._SAFE_GIT_ENV,  # noqa: SLF001
            )
        except BaseException as exc:
            raise StartupNormalizationError(
                "immutable release tracked file check failed"
            ) from exc
        if (
            re.fullmatch(
                rf"100(?:644|755) [0-9a-f]{{40}} 0\t{re.escape(str(relative))}",
                tracked,
            )
            is None
        ):
            raise StartupNormalizationError(
                "normalization dependency is not an exact tracked file"
            )


def confirmation_phrase(request: Mapping[str, Any]) -> str:
    return (
        "normalize-startup:"
        f"{request['operation_id']}:{request['role']}:"
        f"{request['controller_challenge_sha256']}:"
        f"{request['release_sha']}"
    )


def _authority(authority: Callable[[str], bool], checkpoint: str) -> None:
    try:
        permitted = authority(checkpoint)
    except BaseException as exc:
        raise StartupNormalizationCancellation(
            f"controller authority failed at {checkpoint}"
        ) from exc
    if permitted is not True:
        raise StartupNormalizationCancellation(
            f"controller authority was denied at {checkpoint}"
        )


def _cleanup_authority_scope(
    authority: Callable[[str], bool],
):
    factory = getattr(authority, "defer_cancellation", None)
    if factory is None:
        return nullcontext()
    if not callable(factory):
        raise StartupNormalizationError(
            "cleanup authority deferral contract is invalid"
        )
    scope = factory()
    if not hasattr(scope, "__enter__") or not hasattr(scope, "__exit__"):
        raise StartupNormalizationError(
            "cleanup authority deferral scope is invalid"
        )
    return scope


def _check_deferred_authority(
    authority: Callable[[str], bool],
) -> None:
    check = getattr(authority, "check", None)
    if check is None:
        return
    if not callable(check):
        raise StartupNormalizationError(
            "cleanup authority check contract is invalid"
        )
    check()


def plan(
    request_value: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    request = validate_request(request_value, now=now)
    return {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "campaign_id": request["campaign_id"],
        "operation_id": request["operation_id"],
        "release_sha": request["release_sha"],
        "role": request["role"],
        "request_binding_sha256": request["request_binding_sha256"],
        "normalization_invocation_count": 2,
        "database_stop_required": True,
        "redis_start_allowed": False,
        "public_service_start_allowed": False,
        "provider_credentials_allowed": False,
        "background_jobs_allowed": False,
        "live_actions_performed": False,
        "confirmation": confirmation_phrase(request),
    }


def execute(
    request_value: Mapping[str, Any],
    *,
    apply: bool = False,
    confirm: str | None = None,
    authority: Callable[[str], bool] | None = None,
    backend: NormalizationBackend | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    observed_now = (
        datetime.now(timezone.utc)
        if now is None
        else now.astimezone(timezone.utc)
    )
    request = validate_request(request_value, now=observed_now)
    if not apply:
        return plan(request, now=observed_now)
    if (
        confirm != confirmation_phrase(request)
        or authority is None
        or not callable(authority)
    ):
        raise StartupNormalizationError(
            "apply requires exact confirmation and live controller authority"
        )
    if backend is None:
        _verify_execution_context(request)
        active_backend: NormalizationBackend = ExactReleaseBackend(request)
    else:
        active_backend = backend
    current_time = (
        (lambda: datetime.now(timezone.utc))
        if clock is None
        else clock
    )
    captured_at = current_time().astimezone(timezone.utc)
    start_state: StartState | None = None
    before: LogicalState | None = None
    first: LogicalState | None = None
    second: LogicalState | None = None
    stop_state: StopState | None = None
    original: BaseException | None = None
    try:
        _authority(authority, "before-initial-state")
        start_state = (
            active_backend.reconcile_prepared_database_running()
        )
        pre = request["pre_inventory_response"]
        if (
            start_state.database_container_id
            != pre["prepared_container_id"]
            or start_state.network_id != pre["prepared_network_id"]
            or start_state.oneoff_residue_count != 0
            or type(start_state.database_was_running) is not bool
            or type(start_state.database_start_performed) is not bool
            or (
                start_state.database_was_running
                is start_state.database_start_performed
            )
        ):
            raise StartupNormalizationError(
                "database start reconciliation differs from prepared identity"
            )
        before = active_backend.logical_state()
        _authority(authority, "before-first-startup-normalization")
        _normalization_output(
            _canonical_json(active_backend.normalize_once()).decode("ascii"),
            role=request["role"],
        )
        _authority(authority, "before-first-state")
        first = active_backend.logical_state()
        _authority(authority, "before-second-startup-normalization")
        _normalization_output(
            _canonical_json(active_backend.normalize_once()).decode("ascii"),
            role=request["role"],
        )
        _authority(authority, "before-second-state")
        second = active_backend.logical_state()
        if first != second:
            raise StartupNormalizationError(
                "second startup invocation changed database or data"
            )
        _authority(authority, "before-database-stop")
    except BaseException as exc:
        original = exc
    try:
        with _cleanup_authority_scope(authority):
            stop_state = active_backend.stop_operation_containers()
    except BaseException as cleanup_exc:
        if original is not None:
            original.add_note(
                "operation-owned container stop reconciliation also failed"
            )
        else:
            original = cleanup_exc
    try:
        _check_deferred_authority(authority)
    except BaseException as authority_exc:
        if original is not None:
            original.add_note(
                "controller authority was lost during mandatory stop "
                "reconciliation"
            )
        else:
            original = authority_exc
    if original is not None:
        raise original
    if (
        before is None
        or start_state is None
        or first is None
        or second is None
        or stop_state is None
        or stop_state.operation_owned_running_container_count != 0
        or stop_state.oneoff_residue_count != 0
    ):
        raise StartupNormalizationError(
            "normalization did not reach its stopped closure"
        )
    pre = request["pre_inventory_response"]
    if (
        stop_state.database_container_id != pre["prepared_container_id"]
        or stop_state.network_id != pre["prepared_network_id"]
    ):
        raise StartupNormalizationError(
            "stopped resources differ from the prepared identities"
        )
    completed_at = current_time().astimezone(timezone.utc)
    expires_at = _parse_timestamp(request["expires_at"], label="expires_at")
    if (
        captured_at
        < _parse_timestamp(request["issued_at"], label="issued_at")
        or completed_at < captured_at
        or completed_at > expires_at
    ):
        raise StartupNormalizationError(
            "normalization completed outside its authorized time window"
        )
    result: dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "status": "normalized-stopped",
        **{
            field: request[field]
            for field in (
                "campaign_id",
                "operation_id",
                "release_sha",
                "release_tree_sha",
                "role",
                "expected_host",
                "controller_challenge_sha256",
                "issued_at",
                "expires_at",
                "request_binding_sha256",
            )
        },
        "captured_at": _timestamp(captured_at),
        "completed_at": _timestamp(completed_at),
        "pre_inventory_response_sha256": pre["response_sha256"],
        "prepared_container_id": pre["prepared_container_id"],
        "prepared_network_id": pre["prepared_network_id"],
        "prepared_container_identity_sha256": pre[
            "prepared_container_identity_sha256"
        ],
        "prepared_container_metadata_sha256": pre[
            "prepared_container_metadata_sha256"
        ],
        "prepared_network_identity_sha256": pre[
            "prepared_network_identity_sha256"
        ],
        "prepared_network_metadata_sha256": pre[
            "prepared_network_metadata_sha256"
        ],
        "prepared_config_sha256": pre["prepared_config_sha256"],
        "prepared_environment_sha256": pre[
            "prepared_environment_sha256"
        ],
        "prepared_environment_entry_count": pre[
            "prepared_environment_entry_count"
        ],
        "prepared_compose_config_sha256": pre[
            "prepared_compose_config_sha256"
        ],
        "prepared_host_config_sha256": pre[
            "prepared_host_config_sha256"
        ],
        "prepared_mounts_sha256": pre["prepared_mounts_sha256"],
        "prepared_network_attachment_sha256": pre[
            "prepared_network_attachment_sha256"
        ],
        "before_state": before.document(),
        "first_invocation_state": first.document(),
        "second_invocation_state": second.document(),
        "normalization_command_sha256": _sha256(
            NORMALIZATION_PROGRAM.encode("utf-8")
        ),
        "normalization_invocation_count": 2,
        "second_start_database_delta_count": 0,
        "second_start_data_delta_count": 0,
        "provider_credentials_present": False,
        "provider_network_used": False,
        "background_jobs_enabled": False,
        "redis_started": False,
        "public_service_started": False,
        "oneoff_residue_count": 0,
        "database_stop_performed": True,
        "database_was_running_at_reconciliation": (
            start_state.database_was_running
        ),
        "database_start_performed": (
            start_state.database_start_performed
        ),
        "operation_owned_running_container_count": 0,
        "persistent_resource_removed": False,
        "legacy_mutated": False,
        "current_mutated": False,
        "volume_mutated": False,
        "object_storage_used": False,
    }
    result["response_sha256"] = _sha256(_canonical_json(result))
    return validate_result(result, request=request, now=completed_at)


class StdioAuthority:
    """Keep the SSH/local stdin control channel live between checkpoints."""

    _WAKE_SIGNAL = signal.SIGUSR1
    _SIGNALS = (
        signal.SIGHUP,
        signal.SIGINT,
        signal.SIGTERM,
        _WAKE_SIGNAL,
    )

    def __init__(self, request_binding_sha256: str) -> None:
        self.request_binding_sha256 = request_binding_sha256
        self.sequence = 0
        self._cancelled = threading.Event()
        self._cancellation_deferred = threading.Event()
        self._monitor_stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._reason = "controller stdio authority was lost"
        self._old_handlers: dict[int, Any] = {}

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        if signum != self._WAKE_SIGNAL:
            self._reason = f"normalization worker received signal {signum}"
            self._cancelled.set()
        elif not self._cancelled.is_set():
            self._reason = "normalization watchdog cancellation was requested"
            self._cancelled.set()
        if self._cancellation_deferred.is_set():
            return
        raise StartupNormalizationCancellation(self._reason)

    def _watch_control(self) -> None:
        descriptor = sys.stdin.buffer.fileno()
        while not self._monitor_stop.is_set():
            try:
                readable, _, _ = select.select(
                    [descriptor],
                    [],
                    [],
                    0.05,
                )
            except (OSError, ValueError):
                readable = [descriptor]
            if not readable:
                continue
            try:
                payload = os.read(descriptor, 1)
            except OSError:
                payload = b""
            self._reason = (
                "controller stdio carried unsolicited data"
                if payload
                else "controller stdio reached EOF"
            )
            self._cancelled.set()
            main_ident = threading.main_thread().ident
            if main_ident is not None:
                try:
                    signal.pthread_kill(main_ident, self._WAKE_SIGNAL)
                except (OSError, RuntimeError):
                    pass
            return

    def _start_monitor(self) -> None:
        self._monitor_stop.clear()
        self._monitor = threading.Thread(
            target=self._watch_control,
            name="startup-normalization-stdio-liveness",
            daemon=True,
        )
        try:
            self._monitor.start()
        except BaseException:
            self._monitor = None
            raise

    def _stop_monitor(self) -> None:
        self._monitor_stop.set()
        monitor = self._monitor
        self._monitor = None
        if monitor is not None:
            monitor.join(timeout=1.0)
            if monitor.is_alive():
                raise StartupNormalizationError(
                    "controller stdio monitor did not stop"
                )

    def check(self) -> None:
        if self._cancelled.is_set():
            raise StartupNormalizationCancellation(self._reason)

    @contextmanager
    def defer_cancellation(self):
        if self._cancellation_deferred.is_set():
            raise StartupNormalizationError(
                "stdio cancellation deferral cannot be nested"
            )
        self._cancellation_deferred.set()
        try:
            yield
        finally:
            self._cancellation_deferred.clear()

    def __enter__(self) -> StdioAuthority:
        if threading.current_thread() is not threading.main_thread():
            raise StartupNormalizationError(
                "stdio authority requires the main thread"
            )
        try:
            for signum in self._SIGNALS:
                self._old_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, self._handle_signal)
            self.check()
            return self
        except BaseException:
            self._restore()
            raise

    def _restore(self) -> None:
        active_error = sys.exception()
        cleanup_error: BaseException | None = None
        try:
            self._stop_monitor()
        except BaseException as exc:
            cleanup_error = exc
        for signum, handler in reversed(tuple(self._old_handlers.items())):
            try:
                signal.signal(signum, handler)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        self._old_handlers.clear()
        if cleanup_error is not None:
            if active_error is not None:
                active_error.add_note(
                    "stdio authority cleanup also failed: "
                    f"{type(cleanup_error).__name__}"
                )
                return
            raise cleanup_error

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self._restore()

    def __call__(self, checkpoint: str) -> bool:
        self._stop_monitor()
        self.check()
        self.sequence += 1
        challenge = os.urandom(32).hex()
        frame = {
            "schema": AUTHORITY_REQUEST_SCHEMA,
            "sequence": self.sequence,
            "checkpoint": checkpoint,
            "challenge": challenge,
            "request_binding_sha256": self.request_binding_sha256,
        }
        sys.stdout.buffer.write(_canonical_json(frame) + b"\n")
        sys.stdout.buffer.flush()
        raw = sys.stdin.buffer.readline(MAX_CONTROL_BYTES + 2)
        if (
            not raw
            or len(raw) > MAX_CONTROL_BYTES + 1
            or not raw.endswith(b"\n")
        ):
            return False
        try:
            response = json.loads(
                raw[:-1].decode("ascii"),
                object_pairs_hook=_strict_object,
            )
        except (UnicodeError, ValueError, json.JSONDecodeError):
            return False
        accepted = response == {
            "schema": AUTHORITY_RESPONSE_SCHEMA,
            "status": "authorized",
            "sequence": self.sequence,
            "checkpoint": checkpoint,
            "challenge": challenge,
            "request_binding_sha256": self.request_binding_sha256,
        }
        if not accepted:
            return False
        self._start_monitor()
        self.check()
        return True


def _read_initial_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.readline(MAX_CONTROL_BYTES + 2)
    if (
        not raw
        or len(raw) > MAX_CONTROL_BYTES + 1
        or not raw.endswith(b"\n")
    ):
        raise StartupNormalizationError(
            "host request is missing or oversized"
        )
    try:
        value = json.loads(
            raw[:-1].decode("ascii"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise StartupNormalizationError(
            "host request is not strict JSON"
        ) from exc
    if not isinstance(value, dict):
        raise StartupNormalizationError("host request is not an object")
    return value


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host-stdio", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if not args.host_stdio:
            raise StartupNormalizationError(
                "exact --host-stdio mode is required"
            )
        request = _read_initial_request()
        if args.apply:
            validated = validate_request(request)
            with StdioAuthority(
                validated["request_binding_sha256"]
            ) as authority:
                result = execute(
                    validated,
                    apply=True,
                    confirm=args.confirm,
                    authority=authority,
                )
        else:
            if args.confirm is not None:
                raise StartupNormalizationError(
                    "plan mode does not accept confirmation"
                )
            result = plan(request)
        payload = _canonical_json(
            {
                "schema": (
                    "production-shadow-startup-normalization-final-v1"
                ),
                "result": result,
            }
        )
        status = 0
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        payload = _canonical_json(
            {
                "schema": ERROR_SCHEMA,
                "status": "blocked",
                "error": "startup normalization failed closed",
                "error_class": "StartupNormalizationError",
            }
        )
        status = 1
    if len(payload) > MAX_RESPONSE_BYTES:
        payload = _canonical_json(
            {
                "schema": ERROR_SCHEMA,
                "status": "blocked",
                "error": "startup normalization response exceeded its bound",
                "error_class": "StartupNormalizationError",
            }
        )
        status = 1
    sys.stdout.buffer.write(payload + b"\n")
    sys.stdout.buffer.flush()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
