#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import tempfile
from collections import OrderedDict
from pathlib import Path

try:
    from deploy_config import parse_env_file
except ModuleNotFoundError:  # pragma: no cover - used when imported as scripts.render_runtime_envs
    from scripts.deploy_config import parse_env_file


COMMON_RUNTIME_KEYS = (
    "BOT_TOKEN",
    "BOT_USERNAME",
    "DATABASE_URL",
    "SYNC_DATABASE_URL",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "REDIS_URL",
    "JWT_SECRET_KEY",
    "DEV_API_KEY",
    "SYNC_API_KEY",
    "SYNC_VERIFY_TLS",
    "SYNC_CA_BUNDLE",
    "OBSERVABILITY_API_KEY",
    "CHANNEL_ID",
    "CHANNEL_INVITE_LINK",
    "SMSIR_API_KEY",
    "SMSIR_LINE_NUMBER",
    "SMSIR_OTP_TEMPLATE_ID",
    "SMSIR_OTP_TEMPLATE_PARAMETER",
    "SMSIR_INVITATION_TEMPLATE_ID",
    "SMSIR_INVITATION_TEMPLATE_PARAMETER",
    "SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID",
    "SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID",
    "ERROR_TRACKING_DSN",
    "TRUSTED_PROXY_CIDRS",
    "OBSERVABILITY_TELEGRAM_USER_HASH_SALT",
    "GRAFANA_ALERT_DEFAULT_RECEIVER",
    "GRAFANA_ALERT_CRITICAL_RECEIVER",
    "GRAFANA_ALERT_WARNING_RECEIVER",
    "GRAFANA_ALERT_WEBHOOK_URL",
    "GRAFANA_ALERT_EMAIL_ADDRESSES",
    "WEB_PUSH_ENABLED",
    "WEB_PUSH_VAPID_PUBLIC_KEY",
    "WEB_PUSH_VAPID_PRIVATE_KEY",
    "WEB_PUSH_VAPID_SUBJECT",
    "WEB_PUSH_TTL_SECONDS",
    "WEB_PUSH_TIMEOUT_SECONDS",
    "PUBLIC_WEBAPP_URL",
    "FOREIGN_SERVER_ALIASES",
    "IRAN_SERVER_ALIASES",
    "TELEGRAM_DIRECT_REGISTRATION_ENABLED",
    "TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED",
    "TELEGRAM_LOGIN_OTP_ENABLED",
    "OTP_SMS_AUTO_FALLBACK_ENABLED",
    "OTP_SMS_AUTO_FALLBACK_SECONDS",
    "OTP_TTL_SECONDS",
    "IRAN_OTP_DELIVERY_STATE_SECRET",
    "TELEGRAM_OTP_QUEUE_SECRET",
    "TELEGRAM_REGISTRATION_POST_EXPIRY_GRACE_SECONDS",
    "TELEGRAM_REGISTRATION_JOB_BATCH_SIZE",
    "TELEGRAM_REGISTRATION_JOB_CONCURRENCY",
    "OTP_SMS_FALLBACK_JOB_CONCURRENCY",
    "INVITATION_SMS_STANDARD_ENABLED",
    "INVITATION_SMS_CUSTOMER_TIER1_ENABLED",
    "INVITATION_SMS_ACCOUNTANT_ENABLED",
    "INVITATION_SMS_CUSTOMER_TIER2_ENABLED",
    "INVITATION_CONTRACT_V2_ENABLED",
    "REGISTRATION_SYNC_V2_ENABLED",
    "REGISTRATION_SYNC_ACCEPT_UNVERSIONED",
    "INVITATION_PUBLIC_RATE_LIMIT_PER_MINUTE",
    "OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED",
    "RELEASE_SHA",
    # Telegram Queue-v1 global rollout profile and executor credentials.  The
    # complete foreign projection retains these values for the bot service;
    # Docker Compose blanks every provider token in non-bot processes.
    "TELEGRAM_DELIVERY_PRODUCER_MODE",
    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER",
    "TELEGRAM_DELIVERY_EXECUTION_OWNER",
    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED",
    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY",
    "TELEGRAM_MULTI_PUBLISHER_ENABLED",
    "TELEGRAM_B2B_DISPATCH_ENABLED",
    "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED",
    "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
    "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID",
    "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_EDITOR_BOT_ID",
    "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID",
    "TELEGRAM_DELIVERY_QUEUE_SHARED_PUBLISHER_FLEET_ENABLED",
    "TELEGRAM_MONITORING_BOT_TOKEN",
    "TELEGRAM_PROVIDER_TEST_AUTHORITY",
    "TELEGRAM_PUBLISHER_1_ENABLED",
    "TELEGRAM_PUBLISHER_1_BOT_TOKEN",
    "TELEGRAM_PUBLISHER_1_EXPECTED_BOT_ID",
    "TELEGRAM_PUBLISHER_1_EXPECTED_USERNAME",
    "TELEGRAM_PUBLISHER_2_ENABLED",
    "TELEGRAM_PUBLISHER_2_BOT_TOKEN",
    "TELEGRAM_PUBLISHER_2_EXPECTED_BOT_ID",
    "TELEGRAM_PUBLISHER_2_EXPECTED_USERNAME",
    "TELEGRAM_PUBLISHER_3_ENABLED",
    "TELEGRAM_PUBLISHER_3_BOT_TOKEN",
    "TELEGRAM_PUBLISHER_3_EXPECTED_BOT_ID",
    "TELEGRAM_PUBLISHER_3_EXPECTED_USERNAME",
    "TELEGRAM_PUBLISHER_4_ENABLED",
    "TELEGRAM_PUBLISHER_4_BOT_TOKEN",
    "TELEGRAM_PUBLISHER_4_EXPECTED_BOT_ID",
    "TELEGRAM_PUBLISHER_4_EXPECTED_USERNAME",
    "TELEGRAM_PUBLISHER_5_ENABLED",
    "TELEGRAM_PUBLISHER_5_BOT_TOKEN",
    "TELEGRAM_PUBLISHER_5_EXPECTED_BOT_ID",
    "TELEGRAM_PUBLISHER_5_EXPECTED_USERNAME",
    # Production inference is explicitly projected instead of inheriting a
    # host shell.  Defaults remain off and automatic selection remains off.
    "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED",
    "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED",
    "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED",
    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_DIR",
    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_CONTAINER_DIR",
    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_CONTAINER_PATH",
    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_PATH",
    "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS",
    "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED",
)

TELEGRAM_PROVIDER_TOKEN_KEYS = frozenset(
    {
        "BOT_TOKEN",
        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN",
        "TELEGRAM_MONITORING_BOT_TOKEN",
        *(f"TELEGRAM_PUBLISHER_{index}_BOT_TOKEN" for index in range(1, 6)),
    }
)

OPTIONAL_RUNTIME_DEFAULTS = {
    "CHANNEL_INVITE_LINK": "",
    "ERROR_TRACKING_DSN": "",
    "SYNC_VERIFY_TLS": "true",
    "SYNC_CA_BUNDLE": "",
    "SMSIR_OTP_TEMPLATE_ID": "585147",
    "SMSIR_OTP_TEMPLATE_PARAMETER": "CODE",
    "SMSIR_INVITATION_TEMPLATE_ID": "657938",
    "SMSIR_INVITATION_TEMPLATE_PARAMETER": "NAME",
    "SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID": "162103",
    "SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID": "903643",
    "WEB_PUSH_ENABLED": "false",
    "WEB_PUSH_VAPID_PUBLIC_KEY": "",
    "WEB_PUSH_VAPID_PRIVATE_KEY": "",
    "WEB_PUSH_VAPID_SUBJECT": "",
    "WEB_PUSH_TTL_SECONDS": "3600",
    "WEB_PUSH_TIMEOUT_SECONDS": "5.0",
    "PUBLIC_WEBAPP_URL": "",
    "FOREIGN_SERVER_ALIASES": "",
    "IRAN_SERVER_ALIASES": "",
    "TELEGRAM_DIRECT_REGISTRATION_ENABLED": "false",
    "TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED": "false",
    "TELEGRAM_LOGIN_OTP_ENABLED": "false",
    "OTP_SMS_AUTO_FALLBACK_ENABLED": "false",
    "OTP_SMS_AUTO_FALLBACK_SECONDS": "40",
    "OTP_TTL_SECONDS": "120",
    "IRAN_OTP_DELIVERY_STATE_SECRET": "",
    "TELEGRAM_OTP_QUEUE_SECRET": "",
    "TELEGRAM_REGISTRATION_POST_EXPIRY_GRACE_SECONDS": "86400",
    "TELEGRAM_REGISTRATION_JOB_BATCH_SIZE": "10",
    "TELEGRAM_REGISTRATION_JOB_CONCURRENCY": "1",
    "OTP_SMS_FALLBACK_JOB_CONCURRENCY": "4",
    "INVITATION_SMS_STANDARD_ENABLED": "false",
    "INVITATION_SMS_CUSTOMER_TIER1_ENABLED": "false",
    "INVITATION_SMS_ACCOUNTANT_ENABLED": "true",
    "INVITATION_SMS_CUSTOMER_TIER2_ENABLED": "true",
    "INVITATION_CONTRACT_V2_ENABLED": "false",
    "REGISTRATION_SYNC_V2_ENABLED": "false",
    "REGISTRATION_SYNC_ACCEPT_UNVERSIONED": "true",
    "INVITATION_PUBLIC_RATE_LIMIT_PER_MINUTE": "30",
    "OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED": "false",
    "RELEASE_SHA": "",
    "TELEGRAM_DELIVERY_PRODUCER_MODE": "legacy",
    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "legacy",
    "TELEGRAM_DELIVERY_EXECUTION_OWNER": "legacy",
    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "false",
    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "false",
    "TELEGRAM_MULTI_PUBLISHER_ENABLED": "false",
    "TELEGRAM_B2B_DISPATCH_ENABLED": "false",
    "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED": "false",
    "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN": "",
    "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID": "",
    "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_EDITOR_BOT_ID": "",
    "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": "",
    "TELEGRAM_DELIVERY_QUEUE_SHARED_PUBLISHER_FLEET_ENABLED": "false",
    "TELEGRAM_MONITORING_BOT_TOKEN": "",
    "TELEGRAM_PROVIDER_TEST_AUTHORITY": "false",
    **{
        key: default
        for index in range(1, 6)
        for key, default in (
            (f"TELEGRAM_PUBLISHER_{index}_ENABLED", "false"),
            (f"TELEGRAM_PUBLISHER_{index}_BOT_TOKEN", ""),
            (f"TELEGRAM_PUBLISHER_{index}_EXPECTED_BOT_ID", ""),
            (f"TELEGRAM_PUBLISHER_{index}_EXPECTED_USERNAME", ""),
        )
    },
    "PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED": "false",
    "PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED": "false",
    "PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED": "false",
    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_DIR": (
        "/srv/trading-bot/production-data/coin-intelligence/production-runtime"
    ),
    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_CONTAINER_DIR": "/app/runtime/coin-inference",
    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_CONTAINER_PATH": (
        "/app/runtime/coin-inference/coin-rates.json"
    ),
    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_PATH": (
        "/app/runtime/coin-inference/coin-rates.json"
    ),
    "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS": "120",
    "PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED": "false",
}

PERFORMANCE_RUNTIME_DEFAULTS = OrderedDict(
    (
        ("DB_POOL_SIZE", "15"),
        ("DB_MAX_OVERFLOW", "10"),
        ("DB_POOL_RECYCLE_SECONDS", "3600"),
        ("DB_POOL_PRE_PING", "true"),
        ("BACKGROUND_LEADER_LOCK_TTL_SECONDS", "90"),
        ("BACKGROUND_LEADER_LOCK_REFRESH_SECONDS", "30"),
        ("BACKGROUND_LEADER_RETRY_SECONDS", "10"),
    )
)

ROLE_PERFORMANCE_DEFAULTS = {
    "foreign": {
        "DB_POOL_SIZE": "15",
        "DB_MAX_OVERFLOW": "10",
    },
    "iran": {
        "DB_POOL_SIZE": "8",
        "DB_MAX_OVERFLOW": "4",
    },
}

POSTGRES_TUNING_DEFAULTS = OrderedDict(
    (
        ("POSTGRES_MAX_CONNECTIONS", "500"),
        ("POSTGRES_SHARED_BUFFERS", "128MB"),
        ("POSTGRES_EFFECTIVE_CACHE_SIZE", "4GB"),
        ("POSTGRES_WORK_MEM", "4MB"),
        ("POSTGRES_MAINTENANCE_WORK_MEM", "64MB"),
        ("POSTGRES_RANDOM_PAGE_COST", "4"),
        ("POSTGRES_EFFECTIVE_IO_CONCURRENCY", "1"),
        ("POSTGRES_CHECKPOINT_TIMEOUT", "5min"),
        ("POSTGRES_MAX_WAL_SIZE", "1GB"),
        ("POSTGRES_MIN_WAL_SIZE", "80MB"),
        ("POSTGRES_WAL_BUFFERS", "4MB"),
    )
)

REDIS_DURABILITY_DEFAULTS = OrderedDict(
    (
        ("REDIS_APPENDONLY", "yes"),
        ("REDIS_APPENDFSYNC", "everysec"),
        ("REDIS_MAXMEMORY", "0"),
        ("REDIS_MAXMEMORY_POLICY", "noeviction"),
    )
)

ROLE_POSTGRES_TUNING_DEFAULTS = {
    "foreign": {},
    "iran": {
        "POSTGRES_MAX_CONNECTIONS": "150",
        "POSTGRES_SHARED_BUFFERS": "2GB",
        "POSTGRES_EFFECTIVE_CACHE_SIZE": "5GB",
        "POSTGRES_WORK_MEM": "4MB",
        "POSTGRES_MAINTENANCE_WORK_MEM": "256MB",
        "POSTGRES_RANDOM_PAGE_COST": "1.2",
        "POSTGRES_EFFECTIVE_IO_CONCURRENCY": "200",
        "POSTGRES_CHECKPOINT_TIMEOUT": "15min",
        "POSTGRES_MAX_WAL_SIZE": "2GB",
        "POSTGRES_MIN_WAL_SIZE": "512MB",
        "POSTGRES_WAL_BUFFERS": "16MB",
    },
}

# With an immutable production source, process environment pollution must not
# replace credentials, Queue identities, expected IDs/usernames, or inference
# activation.  This allowlist mirrors the intentional operational overrides
# exported by production_deploy_online.sh.  Invocation without a source file
# retains the existing environment-driven development/test behaviour.
SOURCE_ENV_OVERRIDE_KEYS = frozenset(
    {
        "PUBLIC_WEBAPP_URL",
        "FOREIGN_SERVER_ALIASES",
        "IRAN_SERVER_ALIASES",
        "TELEGRAM_DIRECT_REGISTRATION_ENABLED",
        "TELEGRAM_REGISTRATION_RECONCILIATION_ENABLED",
        "TELEGRAM_LOGIN_OTP_ENABLED",
        "OTP_SMS_AUTO_FALLBACK_ENABLED",
        "OTP_SMS_AUTO_FALLBACK_SECONDS",
        "OTP_TTL_SECONDS",
        "TELEGRAM_REGISTRATION_POST_EXPIRY_GRACE_SECONDS",
        "TELEGRAM_REGISTRATION_JOB_BATCH_SIZE",
        "TELEGRAM_REGISTRATION_JOB_CONCURRENCY",
        "OTP_SMS_FALLBACK_JOB_CONCURRENCY",
        "INVITATION_SMS_STANDARD_ENABLED",
        "INVITATION_SMS_CUSTOMER_TIER1_ENABLED",
        "INVITATION_SMS_ACCOUNTANT_ENABLED",
        "INVITATION_SMS_CUSTOMER_TIER2_ENABLED",
        "INVITATION_CONTRACT_V2_ENABLED",
        "REGISTRATION_SYNC_V2_ENABLED",
        "REGISTRATION_SYNC_ACCEPT_UNVERSIONED",
        "INVITATION_PUBLIC_RATE_LIMIT_PER_MINUTE",
        "OFFER_EXPIRY_COMMAND_RECEIPTS_ENABLED",
        "RELEASE_SHA",
        *PERFORMANCE_RUNTIME_DEFAULTS,
        *(f"{role.upper()}_{key}" for role, defaults in ROLE_PERFORMANCE_DEFAULTS.items() for key in defaults),
        *POSTGRES_TUNING_DEFAULTS,
        *(f"{role.upper()}_{key}" for role in ROLE_POSTGRES_TUNING_DEFAULTS for key in POSTGRES_TUNING_DEFAULTS),
        *REDIS_DURABILITY_DEFAULTS,
    }
)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def validate_telegram_rollout_profile(values: dict[str, str]) -> None:
    """Reject split-brain Queue-v1 profiles before runtime files are written."""

    producer = str(values["TELEGRAM_DELIVERY_PRODUCER_MODE"]).strip().lower()
    expected = str(
        values["TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER"]
    ).strip().lower()
    executor = str(values["TELEGRAM_DELIVERY_EXECUTION_OWNER"]).strip().lower()
    if producer not in {"legacy", "queue-v1"}:
        raise SystemExit("TELEGRAM_DELIVERY_PRODUCER_MODE must be legacy or queue-v1")
    if expected != producer or executor != producer:
        raise SystemExit("Telegram producer/expected/executor global profile is split-brain")
    if _truthy(values.get("TELEGRAM_PROVIDER_TEST_AUTHORITY")):
        raise SystemExit("TELEGRAM_PROVIDER_TEST_AUTHORITY is forbidden in production")

    queue_controls = (
        _truthy(values.get("TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED")),
        _truthy(values.get("TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY")),
        _truthy(values.get("TELEGRAM_MULTI_PUBLISHER_ENABLED")),
        _truthy(values.get("TELEGRAM_B2B_DISPATCH_ENABLED")),
    )
    if producer == "legacy" and any(queue_controls):
        raise SystemExit("Legacy Telegram profile rejects Queue-v1 enablement")
    if producer == "queue-v1" and not all(queue_controls):
        raise SystemExit("Queue-v1 Telegram profile requires worker, cutover, multi-publisher, and B2B")
    if _truthy(values.get("PRODUCTION_COIN_INFERENCE_AUTO_SELECTION_ENABLED")):
        raise SystemExit("Production inference automatic selection is not authorized")
    if str(
        values.get("PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS") or ""
    ).strip() != "120":
        raise SystemExit(
            "PRODUCTION_COIN_INFERENCE_MAXIMUM_AGE_SECONDS must be exactly 120"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render foreign and Iran runtime env files.")
    parser.add_argument("--local-output", required=True, help="Output path for the foreign runtime .env")
    parser.add_argument("--iran-output", required=True, help="Output path for the Iran runtime .env")
    parser.add_argument("--foreign-frontend-url", required=True)
    parser.add_argument("--iran-frontend-url", required=True)
    parser.add_argument("--foreign-server-url", required=True)
    parser.add_argument("--foreign-server-domain", required=True)
    parser.add_argument("--iran-server-url", required=True)
    parser.add_argument("--iran-server-domain", required=True)
    parser.add_argument(
        "--source-env-file",
        help=(
            "Optional immutable runtime env source. Source values are authoritative; "
            "only the documented non-sensitive renderer allowlist may be overridden "
            "by the process environment."
        ),
    )
    parser.add_argument("--metrics-backend", default="memory")
    parser.add_argument("--audit-trail-path", default="/app/audit_trail/audit.jsonl")
    parser.add_argument("--foreign-api-workers", default=os.environ.get("FOREIGN_API_WORKERS", "2"))
    parser.add_argument("--iran-api-workers", default=os.environ.get("IRAN_API_WORKERS", "4"))
    return parser.parse_args()

def collect_runtime_values(source_env_file: str | None = None) -> dict[str, str]:
    import os

    source_values = parse_env_file(Path(source_env_file)) if source_env_file else {}
    source_is_authoritative = bool(source_env_file)

    def selected(key: str, default: str | None = None) -> str | None:
        if not source_is_authoritative or key in SOURCE_ENV_OVERRIDE_KEYS:
            environment_value = os.environ.get(key)
            if environment_value is not None:
                return environment_value
        return source_values.get(key, default)

    values: dict[str, str] = {}
    missing: list[str] = []
    for key in COMMON_RUNTIME_KEYS:
        is_optional = key in OPTIONAL_RUNTIME_DEFAULTS
        value = selected(key)
        if isinstance(value, str):
            value = value.strip()
        if (value is None or value == "") and is_optional:
            value = OPTIONAL_RUNTIME_DEFAULTS[key]
        if value is None or (value == "" and not is_optional):
            missing.append(key)
            continue
        values[key] = value
    for key, default in PERFORMANCE_RUNTIME_DEFAULTS.items():
        values[key] = str(selected(key, default))
    for role, defaults in ROLE_PERFORMANCE_DEFAULTS.items():
        prefix = role.upper()
        for key, default in defaults.items():
            role_key = f"{prefix}_{key}"
            values[role_key] = str(selected(role_key, default))
    for key, default in POSTGRES_TUNING_DEFAULTS.items():
        values[key] = str(selected(key, default))
    for role, defaults in ROLE_POSTGRES_TUNING_DEFAULTS.items():
        prefix = role.upper()
        for key, fallback in POSTGRES_TUNING_DEFAULTS.items():
            role_key = f"{prefix}_{key}"
            default = defaults.get(key, fallback)
            values[role_key] = str(selected(role_key, default))
    for key, default in REDIS_DURABILITY_DEFAULTS.items():
        values[key] = str(selected(key, default))
    if missing:
        missing_list = ", ".join(missing)
        raise SystemExit(f"Missing required runtime env inputs: {missing_list}")
    validate_telegram_rollout_profile(values)
    return values


def build_runtime_env(
    *,
    role: str,
    frontend_url: str,
    public_webapp_url: str,
    foreign_server_url: str,
    foreign_server_domain: str,
    iran_server_url: str,
    iran_server_domain: str,
    metrics_backend: str,
    audit_trail_path: str,
    api_workers: str,
    values: dict[str, str],
) -> OrderedDict[str, str]:
    rendered = OrderedDict()
    rendered["SERVER_MODE"] = role
    rendered["API_WORKERS"] = str(api_workers)
    for key in COMMON_RUNTIME_KEYS[:6]:
        rendered[key] = values[key]
    rendered["FRONTEND_URL"] = frontend_url
    for key in COMMON_RUNTIME_KEYS[6:]:
        if key in {"IRAN_OTP_DELIVERY_STATE_SECRET", "TELEGRAM_OTP_QUEUE_SECRET"}:
            continue
        if role != "iran" and key.startswith("SMSIR_"):
            rendered[key] = ""
            continue
        if role != "iran" and key == "OTP_SMS_AUTO_FALLBACK_ENABLED":
            rendered[key] = "false"
            continue
        rendered[key] = values.get(key, OPTIONAL_RUNTIME_DEFAULTS.get(key, ""))
    rendered["OTP_DELIVERY_STATE_SECRET"] = (
        values.get("IRAN_OTP_DELIVERY_STATE_SECRET", "") if role == "iran" else ""
    )
    rendered["TELEGRAM_OTP_QUEUE_SECRET"] = (
        values.get("TELEGRAM_OTP_QUEUE_SECRET", "") if role == "foreign" else ""
    )
    role_prefix = role.upper()
    for key in PERFORMANCE_RUNTIME_DEFAULTS:
        rendered[key] = values.get(f"{role_prefix}_{key}", values[key])
    for key in POSTGRES_TUNING_DEFAULTS:
        rendered[key] = values.get(f"{role_prefix}_{key}", values[key])
    for key in REDIS_DURABILITY_DEFAULTS:
        rendered[key] = values[key]
    rendered["TRADING_BOT_METRICS_BACKEND"] = metrics_backend
    rendered["AUDIT_TRAIL_PATH"] = audit_trail_path
    rendered["FOREIGN_SERVER_URL"] = foreign_server_url
    rendered["FOREIGN_SERVER_DOMAIN"] = foreign_server_domain
    rendered["IRAN_SERVER_URL"] = iran_server_url
    rendered["IRAN_SERVER_DOMAIN"] = iran_server_domain
    rendered["PUBLIC_WEBAPP_URL"] = values.get("PUBLIC_WEBAPP_URL") or public_webapp_url
    producer_mode = str(
        values.get("TELEGRAM_DELIVERY_PRODUCER_MODE", "legacy")
    ).strip().lower()
    non_bot_owner = "producer-only" if producer_mode == "queue-v1" else "legacy"
    rendered["TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER"] = non_bot_owner
    # Preserve the historical foreign API direct-provider path during the
    # code-only Legacy base release.  Queue-v1 clears it, and Iran never gets
    # it.  Publisher/editor/monitoring credentials are never projected through
    # this compatibility key.
    rendered["TELEGRAM_NON_BOT_BOT_TOKEN"] = (
        str(values.get("BOT_TOKEN") or "")
        if role == "foreign" and producer_mode == "legacy"
        else ""
    )
    rendered["TELEGRAM_PROVIDER_TEST_AUTHORITY"] = "false"
    if role == "iran":
        # The Iran runtime is a role projection, not a copy of the foreign bot
        # environment.  It can enqueue Queue-v1 work but can never execute or
        # authenticate a Telegram provider call.
        rendered["TELEGRAM_DELIVERY_EXECUTION_OWNER"] = non_bot_owner
        rendered["TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED"] = "false"
        rendered["TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY"] = "false"
        # Keep producer-routing semantics on Iran: Queue-v1 offer intents must
        # remain unassigned until the foreign feeder selects one of the five
        # healthy publisher lanes.  Credentials and lane execution stay
        # disabled below, so this grants no provider authority.
        rendered["TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED"] = "false"
        for index in range(1, 6):
            rendered[f"TELEGRAM_PUBLISHER_{index}_ENABLED"] = "false"
        for key in TELEGRAM_PROVIDER_TOKEN_KEYS:
            rendered[key] = ""
    return rendered


def _resolved_path(path: str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def validate_render_paths(*, source_path: str | None, local_output: str, iran_output: str) -> None:
    """Reject path aliasing before any runtime env file is written.

    The production secret source is intentionally immutable.  A rendered role
    file is a projection of that source and must never be allowed to replace it.
    ``Path.resolve`` also catches existing symlink aliases.
    """

    local_path = _resolved_path(local_output)
    iran_path = _resolved_path(iran_output)
    if local_path == iran_path:
        raise ValueError("Foreign and Iran runtime outputs must be different files")

    if source_path:
        source = _resolved_path(source_path)
        if source in {local_path, iran_path}:
            raise ValueError("Runtime env source must be different from both runtime outputs")


def write_env_file(path: str, payload: OrderedDict[str, str]) -> None:
    """Atomically replace one rendered runtime file with mode 0600."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in payload.items()]
    body = "\n".join(lines) + "\n"
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    try:
        validate_render_paths(
            source_path=args.source_env_file,
            local_output=args.local_output,
            iran_output=args.iran_output,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    values = collect_runtime_values(args.source_env_file)

    write_env_file(
        args.local_output,
        build_runtime_env(
            role="foreign",
            frontend_url=args.foreign_frontend_url,
            public_webapp_url=args.iran_frontend_url,
            foreign_server_url=args.foreign_server_url,
            foreign_server_domain=args.foreign_server_domain,
            iran_server_url=args.iran_server_url,
            iran_server_domain=args.iran_server_domain,
            metrics_backend=args.metrics_backend,
            audit_trail_path=args.audit_trail_path,
            api_workers=args.foreign_api_workers,
            values=values,
        ),
    )
    write_env_file(
        args.iran_output,
        build_runtime_env(
            role="iran",
            frontend_url=args.iran_frontend_url,
            public_webapp_url=args.iran_frontend_url,
            foreign_server_url=args.foreign_server_url,
            foreign_server_domain=args.foreign_server_domain,
            iran_server_url=args.iran_server_url,
            iran_server_domain=args.iran_server_domain,
            metrics_backend=args.metrics_backend,
            audit_trail_path=args.audit_trail_path,
            api_workers=args.iran_api_workers,
            values=values,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
